"""Unit tests for the liveness heartbeat and its checker (ت-3,
``docs/operational-findings.md`` §3 -- ``framework/observability/heartbeat.py``
+ ``app/ops/healthcheck.py``).

**What this module is guarding.** The finding it closes is not "a worker
crashed"; a crashed worker is already visible (the container exits and
restarts). It is «عاملٌ حيٌّ لا يستهلك» -- a process that stays ``Up`` while
its loop no longer turns, which nothing in ``docker compose ps`` could see.
Every assertion below is about the boundary between those two states:

* a beat proves the loop turned, and NOTHING else writes the file;
* an unreadable path degrades to a logged warning, never to a dead worker
  (an observability aid that can take a service down is a net loss);
* the checker's three exit codes stay distinct, because Docker treats 1 and 2
  identically and only the human reading ``docker inspect`` can tell "stale"
  from "misconfigured" apart -- collapsing them would make a typo in a
  Compose ``test:`` line look exactly like a wedged worker.

Hermetic: ``tmp_path`` only, no Docker and no container. The live proof that
Compose actually runs these commands is recorded in ``docs/log/3.136.md``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from app.framework.observability.heartbeat import (
    HEARTBEAT_PROCESS_NAMES,
    FileHeartbeat,
    NullHeartbeat,
    age_seconds,
    build_heartbeat,
    heartbeat_path,
)
from app.framework.settings.settings import HealthSettings, Settings
from app.ops import healthcheck


# --------------------------------------------------------------------------- #
# heartbeat_path / build_heartbeat                                            #
# --------------------------------------------------------------------------- #
def test_path_is_dir_plus_name_plus_suffix() -> None:
    assert heartbeat_path("/var/run/hb", "memory") == Path("/var/run/hb/memory.heartbeat")


def test_each_process_gets_its_own_file() -> None:
    """The property that stops a dead process from riding on a live sibling's
    beat -- RunPod runs `worker` and `outbox-relay` in ONE container."""
    paths = {heartbeat_path("/hb", name) for name in HEARTBEAT_PROCESS_NAMES}
    assert len(paths) == len(HEARTBEAT_PROCESS_NAMES)


@pytest.mark.parametrize("directory", ["", "   "])
def test_an_empty_directory_disables_the_heartbeat(directory: str) -> None:
    assert heartbeat_path(directory, "memory") is None
    assert isinstance(build_heartbeat(directory, "memory"), NullHeartbeat)


def test_a_configured_directory_builds_a_real_file_heartbeat(tmp_path: Path) -> None:
    beat = build_heartbeat(str(tmp_path), "memory")
    assert isinstance(beat, FileHeartbeat)
    assert beat.path == tmp_path / "memory.heartbeat"


# --------------------------------------------------------------------------- #
# FileHeartbeat.beat                                                          #
# --------------------------------------------------------------------------- #
def test_beat_creates_the_directory_and_the_file(tmp_path: Path) -> None:
    """`mkdir(parents=True)` on every beat, not once at construction: the
    directory lives under /tmp and a host can clear it under a running
    container."""
    target = tmp_path / "does" / "not" / "exist"
    beat = FileHeartbeat(target / "memory.heartbeat")

    beat.beat()

    assert (target / "memory.heartbeat").exists()
    assert age_seconds(target / "memory.heartbeat") is not None


def test_beat_advances_the_mtime(tmp_path: Path) -> None:
    path = tmp_path / "memory.heartbeat"
    beat = FileHeartbeat(path)
    beat.beat()
    os.utime(path, (0, 0))  # pretend the last beat was in 1970
    stale = age_seconds(path)
    assert stale is not None and stale > 1_000_000

    beat.beat()

    fresh = age_seconds(path)
    assert fresh is not None and fresh < 5


def test_a_failing_beat_is_logged_once_and_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file that cannot be written must not kill a worker -- and must not
    spam the log either: a beat runs every 5 s of idle, so logging every
    failure would turn one read-only directory into thousands of lines."""
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory")
    beat = FileHeartbeat(blocker / "memory.heartbeat")

    with caplog.at_level(logging.WARNING):
        beat.beat()
        beat.beat()
        beat.beat()

    failures = [r for r in caplog.records if r.message == "heartbeat.write_failed"]
    assert len(failures) == 1


def test_recovery_after_a_failure_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    beat = FileHeartbeat(blocker / "memory.heartbeat")

    with caplog.at_level(logging.INFO):
        beat.beat()  # fails
        blocker.unlink()  # the obstruction goes away
        beat.beat()  # succeeds

    assert [r.message for r in caplog.records if r.message.startswith("heartbeat.")] == [
        "heartbeat.write_failed",
        "heartbeat.write_recovered",
    ]


def test_null_heartbeat_does_nothing_and_writes_nothing(tmp_path: Path) -> None:
    NullHeartbeat().beat()
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# age_seconds                                                                 #
# --------------------------------------------------------------------------- #
def test_age_of_a_missing_file_is_none_not_infinity(tmp_path: Path) -> None:
    """ "Never beat" and "beat long ago" are different states, and the checker
    reports them with different messages."""
    assert age_seconds(tmp_path / "nope.heartbeat") is None


def test_a_future_mtime_is_clamped_to_zero_not_reported_negative(tmp_path: Path) -> None:
    """A clock that steps backwards must not produce a negative age that
    passes every freshness test silently."""
    path = tmp_path / "memory.heartbeat"
    path.touch()

    assert age_seconds(path, now=path.stat().st_mtime - 10_000) == 0.0


# --------------------------------------------------------------------------- #
# app.ops.healthcheck -- the exit-code decision table                         #
# --------------------------------------------------------------------------- #
def _settings(directory: str, *, max_age_s: int = 300) -> Settings:
    return Settings(health=HealthSettings(heartbeat_dir=directory, heartbeat_max_age_s=max_age_s))


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(healthcheck, "load_settings", lambda: settings)


def test_unknown_process_name_is_a_configuration_fault() -> None:
    """A typo in docker-compose.yml's `test:` line must not read as a dead
    worker. Exit 2 and the message names every valid choice -- and it is
    decided BEFORE settings are loaded, so it holds even where `.env` does
    not."""
    code, message = healthcheck.check("worker-memory")  # the SERVICE name, not the process name

    assert code == 2
    assert "unknown process" in message
    for name in HEARTBEAT_PROCESS_NAMES:
        assert name in message


def test_a_disabled_heartbeat_never_reports_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, _settings(""))

    code, message = healthcheck.check("memory")

    assert code == 2
    assert "HEARTBEAT_DIR" in message


def test_a_missing_file_is_unhealthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_settings(monkeypatch, _settings(str(tmp_path)))

    code, message = healthcheck.check("memory")

    assert code == 1
    assert "no heartbeat yet" in message


def test_a_fresh_beat_is_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_settings(monkeypatch, _settings(str(tmp_path)))
    build_heartbeat(str(tmp_path), "memory").beat()

    code, _ = healthcheck.check("memory")

    assert code == 0


def test_a_stale_beat_is_unhealthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_settings(monkeypatch, _settings(str(tmp_path), max_age_s=60))
    path = tmp_path / "memory.heartbeat"
    path.touch()
    os.utime(path, (0, 0))

    code, message = healthcheck.check("memory")

    assert code == 1
    assert "stale" in message


def test_each_process_is_checked_against_its_OWN_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⭐ The ت-7 lesson, encoded: a live sibling must not be able to satisfy
    a dead process's check. `memory` beating says nothing about `knowledge`."""
    _patch_settings(monkeypatch, _settings(str(tmp_path)))
    build_heartbeat(str(tmp_path), "memory").beat()

    assert healthcheck.check("memory")[0] == 0
    assert healthcheck.check("knowledge")[0] == 1
