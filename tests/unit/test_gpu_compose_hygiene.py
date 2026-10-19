"""gpu-compose hygiene: what no other test of the feature owns (QA, C8).

AC-7.5  every image and every ``FROM`` is pinned (NFR-3).
AC-7.6  no credential, and no environment file sourced, in what the branch added (NFR-4).
AC-8.2  ``docs/stack-commands.md`` names the owner's profile line, the overlay, the preflight.
AC-8.3  the ``AizzakLlmCircuitOpen`` runbook names both places Ollama can be.
AC-8.4  ``docs/design/08-local-runbook.md`` carries the service rows and the VRAM budget.
AC-6.1  the VRAM budget names every consumer with a label, and its arithmetic is right.

Text and YAML only: no Docker, no network. The documents are read as the files they are, so a
rewrite that drops a sentence these tests name fails here, not in a reader's confusion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_NBH = "\u2011"  # the non-breaking hyphen of the documents' identifiers (CLAUDE.md)
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.gpu.yml")
_DOCKERFILES = ("Dockerfile", "services/embedding/Dockerfile")


def _text(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


# ── AC-7.5: pinned images ───────────────────────────────────────────────────

# `${<NAME>_IMAGE_REPO:-<repo>}:${<NAME>_IMAGE_TAG:-<tag>}`: a platform image, named by two
# variables whose defaults are this machine's own image name and a tag that is not `latest`.
_PLATFORM_IMAGE = re.compile(
    r"\$\{(?P<name>[A-Z][A-Z0-9_]*)_IMAGE_REPO:-(?P<repo>[^}:$]+)\}"
    r":\$\{(?P=name)_IMAGE_TAG:-(?P<tag>[^}:$]+)\}"
)
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _is_pinned(ref: str) -> bool:
    platform = _PLATFORM_IMAGE.fullmatch(ref)
    if platform:
        return platform["tag"] != "latest"
    if "$" in ref:
        return False
    if _DIGEST.search(ref):
        return True
    name, separator, tag = ref.rpartition(":")
    return bool(separator and name and tag and "/" not in tag and tag != "latest")


def _images_in(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                found.append(value)
            else:
                found += _images_in(value)
    elif isinstance(node, list):
        for value in node:
            found += _images_in(value)
    return found


def _from_images(dockerfile: str) -> list[str]:
    """Every base image a Dockerfile names (a later stage's reference to an earlier stage is
    not one), with ``ARG`` defaults substituted."""
    args: dict[str, str] = {}
    stages: set[str] = set()
    bases: list[str] = []
    for raw in dockerfile.splitlines():
        line = raw.strip()
        arg = re.fullmatch(r"ARG\s+(\w+)=(\S+)", line)
        if arg:
            args[arg[1]] = arg[2]
        if not line.upper().startswith("FROM "):
            continue
        tokens = [t for t in line.split()[1:] if not t.startswith("--")]
        image = re.sub(r"\$\{?(\w+)\}?", lambda m: args.get(m[1], m[0]), tokens[0])
        if image.lower() not in stages:
            bases.append(image)
        if len(tokens) >= 3 and tokens[1].upper() == "AS":
            stages.add(tokens[2].lower())
    return bases


def test_the_pin_classifier_tells_a_pin_from_a_floating_reference() -> None:
    pinned = [
        "postgres:16",
        "ollama/ollama:0.34.4",
        "gcr.io/cadvisor/cadvisor:v0.55.1",
        "registry.local:5000/team/image:1.2",
        "alpine@sha256:" + "a" * 64,
        "${APP_IMAGE_REPO:-aizzak/app}:${APP_IMAGE_TAG:-dev}",
        "${EMBEDDING_CUDA_IMAGE_REPO:-aizzak/embedding-cuda}:${EMBEDDING_CUDA_IMAGE_TAG:-dev}",
    ]
    floating = [
        "postgres",
        "postgres:latest",
        "postgres:",
        "registry.local:5000/team/image",
        "${APP_IMAGE_REPO:-aizzak/app}:${APP_IMAGE_TAG:-latest}",
        "${APP_IMAGE_REPO:-aizzak/app}:${OTHER_IMAGE_TAG:-dev}",
        "${APP_IMAGE_REPO:-aizzak/app}",
        "${IMAGE}",
        "x/y:${TAG}",
    ]
    assert [r for r in pinned if not _is_pinned(r)] == []
    assert [r for r in floating if _is_pinned(r)] == []


def test_the_from_parser_skips_stage_references_and_resolves_arg_defaults() -> None:
    sample = (
        "ARG PY=3.12-slim\n"
        "FROM --platform=linux/amd64 python:${PY} AS builder\n"
        "FROM builder AS tester\n"
        "FROM debian:12\n"
    )
    assert _from_images(sample) == ["python:3.12-slim", "debian:12"]


@pytest.mark.parametrize("name", _COMPOSE_FILES)
def test_every_compose_image_is_pinned(name: str) -> None:
    refs = _images_in(yaml.safe_load(_text(name)))
    from_lines = re.findall(r"^\s*image:\s*(\S+)", _text(name), re.MULTILINE)
    assert refs, "the image scan matched nothing"
    assert set(refs) == set(from_lines), "the YAML walk and the line scan disagree"
    assert [r for r in refs if not _is_pinned(r)] == []


def test_the_compose_files_name_both_kinds_of_image() -> None:
    refs = [r for name in _COMPOSE_FILES for r in _images_in(yaml.safe_load(_text(name)))]
    assert len(refs) >= 30
    assert any(_PLATFORM_IMAGE.fullmatch(r) for r in refs), "the platform images"
    assert "ollama/ollama:0.34.4" in refs and "python:3.12-slim" in refs
    cuda = [r for r in refs if "EMBEDDING_CUDA" in r]
    assert len(cuda) == 1, "the overlay names the CUDA image once"


@pytest.mark.parametrize("name", _DOCKERFILES)
def test_every_dockerfile_base_is_pinned(name: str) -> None:
    bases = _from_images(_text(name))
    assert bases, "the FROM scan matched nothing"
    assert [b for b in bases if not _is_pinned(b)] == [], bases


# ── AC-7.6: no credential, no environment file sourced ──────────────────────

# A real token has a body, so each pattern asks for one. The Docker Hub prefix alone is not a
# token: it appears, correctly, in the test that checks a document is free of it and in the
# delivery notes that describe this very scan.
_CREDENTIALS = {
    "Docker Hub token": re.compile(r"dckr_pat_[A-Za-z0-9_-]{16,}"),
    "Hugging Face token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}"),
    "AWS access key id": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
# `source .env`, `. ./.env`, `set -a; . "$ROOT/.env"`.
_SOURCES_ENV_FILE = re.compile(r"(?:^|[\s;&|(])(?:source|\.)\s+\S*\.env\b", re.MULTILINE)
# Placeholders `.env.example` is allowed to carry for a secret; the second is its documented
# dummy credential for the throwaway test bucket.
_PLACEHOLDER = re.compile(r"change-me[\w-]*")
_DUMMY_VALUES = frozenset({"aizzak-test-secret"})
_SECRET_NAME = re.compile(r"^[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*=(.*)$", re.M)

# Every file the branch added or changed, explicitly: a rename or a new file is a decision to
# make here, not a silent exemption.
_BRANCH_FILES = (
    ".env.example",
    "deploy/gpu-preflight.sh",
    "deploy/ollama/pull_models.py",
    "deploy/prometheus/alerts.yml",
    "deploy/release/build-push.sh",
    "docker-compose.gpu.yml",
    "docker-compose.test.yml",
    "docker-compose.wsl-gpu.yml",
    "docker-compose.yml",
    "docs/delivery/gpu-compose/design.md",
    "docs/delivery/gpu-compose/requirements.md",
    "docs/delivery/gpu-compose/status.md",
    "docs/delivery/gpu-compose/stories.md",
    "docs/delivery/gpu-compose/test-plan.md",
    "docs/deploy-server.md",
    "docs/design/08-local-runbook.md",
    "docs/quickstart.md",
    "docs/runbooks/alerts.md",
    "docs/stack-commands.md",
    "services/embedding/Dockerfile",
    "services/embedding/app.py",
    "services/embedding/requirements-cuda.txt",
    "tests/eval/README.md",
    "tests/eval/vector_parity.py",
    "tests/unit/test_compose_gpu_overlay.py",
    "tests/unit/test_compose_local_llm.py",
    "tests/unit/test_embedding_cuda_image.py",
    "tests/unit/test_embedding_service_app.py",
    "tests/unit/test_gpu_compose_hygiene.py",
    "tests/unit/test_gpu_preflight.py",
    "tests/unit/test_healthcheck_coverage.py",
    "tests/unit/test_ollama_pull_models.py",
    "tests/unit/test_release_build_push.py",
    "tests/unit/test_release_build_push_edges.py",
    "tests/unit/test_resource_budget.py",
    "tests/unit/test_vector_parity.py",
)
_SCRIPTS = (
    "deploy/gpu-preflight.sh",
    "deploy/ollama/pull_models.py",
    "deploy/release/build-push.sh",
    "deploy/release/bundle.sh",
    "deploy/release/install.sh",
)
# Files created by the branch, where a literal secret has no history to excuse it.
_NEW_MACHINE_READ_FILES = (
    "deploy/gpu-preflight.sh",
    "deploy/ollama/pull_models.py",
    "docker-compose.gpu.yml",
    "services/embedding/requirements-cuda.txt",
)


def _credential_hits(text: str) -> list[str]:
    return [label for label, pattern in _CREDENTIALS.items() if pattern.search(text)]


def test_the_credential_scan_finds_what_it_is_for_and_only_that() -> None:
    # Built at run time, so no token-shaped text sits in this file.
    positives = {
        "Docker Hub token": "dckr_" + "pat_" + "a1B2" * 7,
        "Hugging Face token": "hf_" + "aB3" * 10,
        "OpenAI-style key": "sk-" + "Zz9" * 10,
        "GitHub token": "ghp_" + "x" * 36,
        "AWS access key id": "AKIA" + "ABCDEFGH12345678",
        "private key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    }
    for label, secret in positives.items():
        assert _credential_hits(f"token = {secret}") == [label], label
    benign = [
        "dckr_pat_|hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}",  # the patterns themselves
        "the dckr_pat_ prefix",
        "sk-short",
        "hf_model_name",
        "AKIA is a prefix",
    ]
    assert [t for t in benign if _credential_hits(t)] == []
    assert _SOURCES_ENV_FILE.search("set -a; . ./.env; set +a")
    assert _SOURCES_ENV_FILE.search('source "$ROOT/.env"')
    assert not _SOURCES_ENV_FILE.search("see docs/stack-commands.md. Then read .env.example")


def test_the_branch_file_list_names_files_that_exist() -> None:
    missing = [f for f in _BRANCH_FILES if not (_ROOT / f).is_file()]
    assert missing == []
    assert len(set(_BRANCH_FILES)) == len(_BRANCH_FILES)


@pytest.mark.parametrize("relative", _BRANCH_FILES)
def test_no_credential_in_what_the_branch_added_or_changed(relative: str) -> None:
    assert _credential_hits(_text(relative)) == [], relative


@pytest.mark.parametrize("relative", _SCRIPTS)
def test_no_script_sources_an_environment_file(relative: str) -> None:
    assert not _SOURCES_ENV_FILE.search(_text(relative)), relative


def test_env_example_carries_placeholders_for_secrets_and_nothing_else() -> None:
    values = _SECRET_NAME.findall(_text(".env.example"))
    assert len(values) >= 10, "the secret scan matched nothing"
    real = [v for v in values if v and not _PLACEHOLDER.fullmatch(v) and v not in _DUMMY_VALUES]
    assert real == []


@pytest.mark.parametrize("relative", _NEW_MACHINE_READ_FILES)
def test_the_new_machine_read_files_hold_no_secret_assignment(relative: str) -> None:
    text = _text(relative)
    assignment = re.compile(
        r"""(?ix)\b\w*(?:password|secret|token|api_?key)\w*["']?\s*[:=]\s*["']?([^\s"'$<>{}()\[\]#,]{8,})"""
    )
    # `OLLAMA_PULL_CONNECT_WAIT_S`-style names never match; a value that is a plain word of a
    # message (`is`, `not`) is shorter than eight characters or is not an assignment at all.
    assert assignment.findall(text) == [], relative


# ── AC-8.2 ──────────────────────────────────────────────────────────────────


def test_stack_commands_names_the_owner_line_the_overlay_and_the_preflight() -> None:
    text = _text("docs/stack-commands.md")
    assert "COMPOSE_PROFILES=wsl-ollama" in text
    assert "COMPOSE_FILE" in text and "docker-compose.build.yml" in text, "line 1.1 is kept"
    assert "docker-compose.gpu.yml" in text
    assert "gpu-preflight.sh" in text
    # a `--profile X` on a command line replaces COMPOSE_PROFILES instead of adding to it
    replaces = re.findall(r"--profile X`[^\n]*يحلّ محلّ `COMPOSE_PROFILES`", text)
    assert len(replaces) >= 2, "said at the owner's line (1.1) and again for the server (41)"
    assert "### ز) الكرت على خادم" in text
    for number in (40, 41, 42, 43):
        assert re.search(rf"^\| {number} \|", text, re.MULTILINE), number
    assert "ولا يُركَّب الملفّان معاً" in text, "the WSL file and the GPU file are never combined"


# ── AC-8.3 ──────────────────────────────────────────────────────────────────


def _circuit_section() -> str:
    text = _text("docs/runbooks/alerts.md")
    start = text.index("### `AizzakLlmCircuitOpen`")
    end = text.index('<a id="', start)
    return text[start:end]


def test_the_circuit_runbook_names_both_ollama_paths() -> None:
    section = _circuit_section()
    assert "docker compose ps ollama-bridge" in section, "the WSL machine"
    assert re.search(r"docker compose ps ollama\s*$", section, re.MULTILINE), "a server"
    assert "docker compose logs --since 15m ollama" in section
    assert "COMPOSE_PROFILES=wsl-ollama" in section, "a forgotten profile line is the likely cause"
    assert "docker compose run --rm ollama-pull" in section


def test_the_alert_response_text_names_both_ollama_paths() -> None:
    alerts = yaml.safe_load(_text("deploy/prometheus/alerts.yml"))
    rules = [r for g in alerts["groups"] for r in g["rules"] if r.get("alert")]
    rule = next(r for r in rules if r["alert"] == "AizzakLlmCircuitOpen")
    response = " ".join(str(rule["annotations"]["response"]).split())
    assert "docker compose ps ollama-bridge" in response
    assert "docker compose ps ollama" in response
    assert "docker compose logs ollama" in response


# ── AC-8.4 ──────────────────────────────────────────────────────────────────


def test_the_runbook_service_table_places_the_bridge_and_the_new_services() -> None:
    text = _text("docs/design/08-local-runbook.md")
    services_table = text.splitlines()[:60]
    rows = {
        name: line
        for line in reversed(services_table)
        for name in ("ollama-bridge", "ollama", "ollama-pull")
        if line.startswith(f"| `{name}` | ")
    }
    assert set(rows) == {"ollama-bridge", "ollama", "ollama-pull"}
    assert "profile `wsl-ollama`" in rows["ollama-bridge"]
    assert "profile `local-llm`" in rows["ollama"]
    assert "ollama/ollama:0.34.4" in rows["ollama"]
    assert "profile `local-llm`" in rows["ollama-pull"]
    ledger = re.search(r"resources\.standing_cpus\s*=\s*(\S+)", text)
    memory = re.search(r"resources\.standing_memory_gb\s*=\s*(\S+)", text)
    assert ledger and memory and (ledger[1], memory[1]) == ("35.60", "57.28")
    assert f"### 2{_NBH}ي ميزانيّةُ ذاكرة الكرت" in text, "the VRAM budget sits by the ledger"
    assert text.index(f"### 2{_NBH}ي") > text.index(f"### 2{_NBH}ز")


# ── AC-6.1: the VRAM budget ─────────────────────────────────────────────────


def _vram_section() -> str:
    text = _text("docs/design/08-local-runbook.md")
    start = text.index(f"### 2{_NBH}ي ميزانيّةُ ذاكرة الكرت")
    return text[start : text.index("\n## 3) الإقلاع", start)]


def _table_rows(section: str, header_start: str) -> list[list[str]]:
    lines = section.splitlines()
    first = next(i for i, ln in enumerate(lines) if ln.startswith(header_start))
    rows: list[list[str]] = []
    for line in lines[first + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def test_the_vram_budget_names_every_consumer_with_a_label() -> None:
    rows = _table_rows(_vram_section(), "| المستهلك |")
    wanted = (
        "أوزان `gemma3:1b`",
        "ذاكرة السياق",
        "ذاكرة الحساب في Ollama",
        "سياق CUDA لـOllama",
        "أوزان التضمين",
        "تنشيطات التضمين",
        "سياق CUDA لـtorch",
        "**مجموع الخادم**",
    )
    first_column = [row[0] for row in rows]
    assert [w for w in wanted if not any(c.startswith(w) for c in first_column)] == []
    for row in rows:
        assert len(row) == 5, row
        consumer, formula, value, _source, status = row
        assert value.strip() and (formula.strip() or consumer.startswith("**مجموع")), row
        if consumer.startswith(("سطح مكتب", "أوزان `gemma3")):
            assert status == "مقيس", row
        else:
            assert status.startswith("تقدير"), ("every unmeasured value says so", row)
    measured = [row[0] for row in rows if row[4] == "مقيس"]
    assert len(measured) == 2, "only the weights and the desktop are measured, and say so"


def test_the_vram_budget_arithmetic_is_right() -> None:
    section = _vram_section()
    kv_bytes = 2 * 8192 * 26 * (256 + 256) * 2  # parallel x num_ctx x layers x (K+V) x fp16
    assert kv_bytes == 436_207_616
    assert "26,624" in section and "436 مليون بايت (416 م.ب)" in section
    assert kv_bytes / 2**20 == pytest.approx(416.0, abs=0.01)
    assert pytest.approx(777.5, abs=0.05) == 815_319_791 / 2**20 and "777.5" in section
    # the server total is the sum of its rows, in GiB, to the precision the table gives
    total_gib = (777.5 + 416) / 1024 + 0.5 + 0.4 + 0.23 + 0.15 + 0.5
    assert total_gib == pytest.approx(2.9, abs=0.06)
    assert "نحو 2.9 ج.ب" in section

    rows = _table_rows(section, "| الكرت |")
    assert [r[0].split(" (")[0] for r in rows] == ["8 ج.ب", "16 ج.ب", "24 ج.ب"]
    for card, ninety, server, surge, _desktop in rows:
        capacity = int(re.search(r"\((\d+) م\.ب", card)[1])  # type: ignore[index]
        assert int(re.search(r"\d+", ninety)[0]) == round(0.9 * capacity), (card, ninety)  # type: ignore[index]
        server_pct = int(re.search(r"(\d+)%", server)[1])  # type: ignore[index]
        surge_pct = int(re.search(r"(\d+)%", surge)[1])  # type: ignore[index]
        assert abs(server_pct - 2.9 * 1024 / capacity * 100) <= 1.0, (card, server)
        assert abs(surge_pct - (2.9 + 0.9) * 1024 / capacity * 100) <= 1.0, (card, surge)
    assert [int(r[0].split("(")[1].split()[0]) for r in rows] == [8188, 16384, 24576]


def test_the_vram_budget_states_its_measurement_method_and_the_conditions() -> None:
    section = _vram_section()
    assert "OLLAMA_NUM_PARALLEL + 2" in section, "a load heavier than the per-process guard"
    assert "size_vram" in section and "OOMKilled" in section and "0.90" in section
    assert "OLLAMA_MAX_LOADED_MODELS=1" in section
    assert "replicas: 1" in section, "one embedding process on the card, and why"


# ── the template stays commented ────────────────────────────────────────────


def test_env_example_activates_no_compose_selector() -> None:
    lines = _text(".env.example").splitlines()
    active: list[Any] = [ln for ln in lines if re.match(r"^(COMPOSE_[A-Z_]+|OLLAMA_BASE_URL)=", ln)]
    assert active == [], "CI copies this file as its environment; the server lines stay commented"


# ── the delivery's own coverage matrix ──────────────────────────────────────


def test_the_coverage_matrix_names_every_ac_of_the_stories_exactly_once() -> None:
    """test-plan.md is only worth reading if it is complete: one row per AC of stories.md, each
    ending in a state. (A red state is allowed -- it is information -- an absent row is not.)"""
    stories = _text("docs/delivery/gpu-compose/stories.md")
    defined = re.findall(rf"\*\*AC{_NBH}(\d+\.\d+)\*\*", stories)
    assert len(defined) == len(set(defined)) == 64
    plan = _text("docs/delivery/gpu-compose/test-plan.md")
    rows = re.findall(rf"^\| AC{_NBH}(\d+\.\d+) \|.*\| (\S+)[^|]* \|$", plan, re.MULTILINE)
    assert sorted(ac for ac, _ in rows) == sorted(defined)
    assert [ac for ac, state in rows if state not in {"✅", "⏸", "⏳", "🔴"}] == []
