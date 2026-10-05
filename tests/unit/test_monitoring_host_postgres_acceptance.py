"""Acceptance guards for `monitoring-host-postgres` that no other test owns
(docs/delivery/monitoring-host-postgres/test-plan.md).

Three groups, each closing an acceptance criterion that was only reviewed by
eye before:

* AC-4.5 / AC-5.7 -- the runbook sections of the four new rules carry what an
  operator needs at 3 a.m. (meaning, numbered steps whose first command only
  reads, the prune warning, how to know it cleared, an index row).
* AC-9.1 / AC-8.5 -- the activation procedure and the status ledgers say what
  the stories require.
* AC-1.1 / AC-2.3 -- what `docker compose config` itself reports, rendered
  from `.env.example` only (never `.env`), skipped when Docker is absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK = _ROOT / "docs" / "runbooks" / "alerts.md"
_LOCAL_RUNBOOK = _ROOT / "docs" / "design" / "08-local-runbook.md"
_ALERTS = _ROOT / "deploy" / "prometheus" / "alerts.yml"

_NEW_RULES = (
    "AizzakHostDiskHigh",
    "AizzakPostgresDown",
    "AizzakPostgresLockWaitHigh",
    "AizzakPostgresArchiveStalled",
)
# The first command of each runbook section must only read (AC-4.5, AC-5.7).
_FIRST_COMMAND = {
    "AizzakHostDiskHigh": "df -h /",
    "AizzakPostgresDown": "docker compose ps postgres",
    "AizzakPostgresLockWaitHigh": "pg_blocking_pids",
    "AizzakPostgresArchiveStalled": "pg_stat_archiver",
}
_MUTATING = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|TRUNCATE|ALTER|pg_terminate_backend|"
    r"pg_cancel_backend|restart|rm -|prune)\b",
    re.IGNORECASE,
)


def _section(name: str) -> str:
    text = _RUNBOOK.read_text(encoding="utf-8")
    start = text.index(f'<a id="{name.lower()}"></a>')
    end = text.find('<a id="', start + 1)
    return text[start : end if end != -1 else len(text)]


def _first_fenced_block(section: str) -> str:
    match = re.search(r"```(?:bash|sql)?\n(.*?)```", section, re.DOTALL)
    assert match, "no fenced command in the section"
    return match.group(1)


@pytest.mark.parametrize("alert", _NEW_RULES)
def test_each_new_runbook_section_is_complete(alert: str) -> None:
    """AC-4.5 / AC-5.7: meaning, numbered steps, a read-only first command,
    a 'how you know it cleared' line, and a row in the section-1 index."""
    section = _section(alert)
    assert "**ماذا يعني:**" in section
    assert "**الخطوات:**" in section
    assert re.search(r"^1\. ", section, re.MULTILINE), "no numbered first step"
    assert re.search(r"^2\. ", section, re.MULTILINE), "steps are not a sequence"
    assert "**كيف تعرف أنّه زال:**" in section
    first = _first_fenced_block(section)
    assert _FIRST_COMMAND[alert] in first, (
        f"first command of {alert} is not {_FIRST_COMMAND[alert]}"
    )
    assert not _MUTATING.search(first), f"first command of {alert} is not read-only: {first!r}"
    index_row = f"[`{alert}`](#{alert.lower()})"
    assert index_row in _RUNBOOK.read_text(encoding="utf-8"), f"no index row for {alert}"


def test_the_disk_runbook_forbids_the_three_data_destroying_commands() -> None:
    section = _section("AizzakHostDiskHigh")
    for command in ("docker volume prune", "docker system prune", "docker compose down -v"):
        assert re.search(rf"لا\W+`{re.escape(command)}`", section), f"{command} not forbidden"
    assert "docker system df" in section


@pytest.mark.parametrize("alert", _NEW_RULES)
def test_no_new_runbook_step_restarts_postgres_without_saying_it_is_human(alert: str) -> None:
    """AC-5.7: restarting `postgres` is the human's decision, never a step."""
    for line in _section(alert).splitlines():
        if re.search(r"restart[^\n]*postgres|إعادةُ? تشغيل `?postgres", line):
            assert "بشريّ" in line, f"{alert}: {line!r}"


def test_the_disk_runbook_admits_the_windows_drive_is_not_watched() -> None:
    """US-4 edge: the guide says the host drive can fill first (Q-2)."""
    section = _section("AizzakHostDiskHigh")
    assert "/mnt/c" in section and "غيرُ مراقَب" in section


# ── AC-9.1 · the activation procedure ──────────────────────────────────────


def _activation() -> str:
    text = _LOCAL_RUNBOOK.read_text(encoding="utf-8")
    start = text.index("### 3.3\u2011ج")
    end = text.find("\n### ", start + 1)
    end2 = text.find("\n## ", start + 1)
    stop = min(x for x in (end, end2, len(text)) if x != -1)
    return text[start:stop]


def test_the_activation_steps_come_in_the_required_order() -> None:
    """AC-9.1: password, then the role, then the two containers without
    recreating postgres, then Prometheus, then the verification queries."""
    s = _activation()
    marks = [
        s.index("METRICS_EXPORTER_PASSWORD"),
        s.index("15-metrics-exporter.sh"),
        s.index("up -d --no-deps node-exporter postgres-exporter"),
        s.index("--force-recreate --no-deps prometheus"),
        s.index('up{job=~"node|postgres"}'),
    ]
    assert marks == sorted(marks), marks


def test_the_activation_warns_that_a_plain_up_recreates_things() -> None:
    s = _activation()
    assert "`docker compose up -d` العامّ" in s
    assert "no-deps" in s and "docker compose build" not in s.replace("لا تُبنى", "")


def test_the_activation_explains_recreate_over_reload_and_checks_postgres_is_untouched() -> None:
    s = _activation()
    assert "inode" in s and "-/reload" not in s.replace("لا reload", "")
    assert "docker inspect -f '{{.Id}} {{.State.StartedAt}}' aizzak-postgres-1" in s
    assert "docker compose config --hash postgres" in s


def test_the_activation_prints_no_secret() -> None:
    """The password crosses stdin only: never echoed, never on a command line."""
    s = _activation()
    assert (
        "docker compose exec -T postgres bash -c" in s and "read -r METRICS_EXPORTER_PASSWORD" in s
    )
    for line in s.splitlines():
        if line.lstrip().startswith(("echo", "printf")) and "METRICS_EXPORTER_PASSWORD" in line:
            assert ">> .env" in line or "grep -q" in line, f"password may be printed: {line!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG-1 (test-plan.md): AC-9.1 step 0 requires a password-LENGTH check "
        "without printing it (08-local-runbook.md:665-675 style); the section "
        "only generates the value when the name is absent, so a copied "
        "`change-me-*` placeholder passes silently."
    ),
)
def test_the_activation_checks_the_password_length_without_printing_it() -> None:
    s = _activation()
    assert re.search(r"\$\{#|wc -c|awk[^\n]*length|len=", s), "no length check in the procedure"


# ── AC-8.5 · the ledgers ────────────────────────────────────────────────────


def test_the_monitoring_plan_counts_the_real_number_of_rules() -> None:
    """AC-8.5: `monitoring-plan.md` names the live rule count and no longer
    lists the host and Postgres itself as missing."""
    rules = sum(
        len(g["rules"]) for g in yaml.safe_load(_ALERTS.read_text(encoding="utf-8"))["groups"]
    )
    plan = (_ROOT / "docs" / "monitoring-plan.md").read_text(encoding="utf-8")
    alerts_row = next(line for line in plan.splitlines() if line.startswith("| التنبيهات"))
    assert f"{rules} قاعدة" in alerts_row, f"plan row does not say {rules} rules"
    numbers_row = next(line for line in plan.splitlines() if line.startswith("| الأرقام"))
    existing, missing = numbers_row.split("|")[2], numbers_row.split("|")[3]
    assert "Postgres نفسه" in existing and "Postgres نفسه" not in missing
    assert "الجهاز" in existing and "الجهاز" not in missing


def test_the_capacity_ledger_says_what_the_exporter_closed_and_left_open() -> None:
    status = (_ROOT / "docs" / "capacity-status.md").read_text(encoding="utf-8")
    for debt in ("د\u201112", "د\u201115"):
        row = next(line for line in status.splitlines() if line.startswith(f"| **{debt}**"))
        assert "monitoring-host-postgres" in row, f"{debt} ignores the exporter"
        assert "postgres-exporter" in row or "postgres_exporter" in row, debt
        assert "ويبقى" in row or "ولا قاعدةَ" in row, f"{debt} hides what stays open"


# ── AC-1.1 · AC-2.3 · what Compose itself says ─────────────────────────────

_needs_compose = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not installed"
)


def _compose(env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("METRICS_EXPORTER")}
    return subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "config", *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@_needs_compose
def test_compose_renders_both_exporters_as_standing_services_with_limits() -> None:
    """AC-1.1 / AC-3.1, from the rendered model rather than the raw YAML."""
    done = _compose(_ROOT / ".env.example", "--format", "json")
    if done.returncode != 0 and "docker compose" in done.stderr and "unknown" in done.stderr:
        pytest.skip("docker compose plugin unavailable")
    assert done.returncode == 0, done.stderr
    services: dict[str, Any] = json.loads(done.stdout)["services"]
    for name in ("node-exporter", "postgres-exporter"):
        svc = services[name]
        limits = svc["deploy"]["resources"]["limits"]
        assert limits.get("cpus") and limits.get("memory"), name
        assert "profiles" not in svc and "ports" not in svc, name
        assert ":" in svc["image"] and not svc["image"].endswith(":latest"), name
        assert str(svc.get("user", "")).split(":")[0] not in ("root", "0"), name
        for volume in svc.get("volumes", []):
            assert "docker.sock" not in str(volume.get("source")), name
            if volume.get("type") == "bind":
                assert volume.get("read_only") is True, f"{name}: writable host mount"
    assert services["postgres-exporter"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    environment = services["postgres-exporter"]["environment"]
    assert "POSTGRES_SUPERUSER_PASSWORD" not in environment
    assert "APP_RW_PASSWORD" not in environment


@_needs_compose
def test_compose_refuses_to_render_without_the_exporter_password(tmp_path: Path) -> None:
    """AC-2.3: a `.env` that lacks the variable fails loudly and names it."""
    source = (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    env_file = tmp_path / "env"
    env_file.write_text(
        "\n".join(x for x in source if not x.startswith("METRICS_EXPORTER_PASSWORD=")) + "\n",
        encoding="utf-8",
    )
    done = _compose(env_file, "--quiet")
    assert done.returncode != 0
    assert "METRICS_EXPORTER_PASSWORD" in done.stderr


@_needs_compose
def test_compose_renders_clean_from_the_example_env() -> None:
    """AC-1.1: `docker compose --env-file .env.example config --quiet` exits 0."""
    done = _compose(_ROOT / ".env.example", "--quiet")
    assert done.returncode == 0, done.stderr
