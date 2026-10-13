"""Documentation and credential hygiene for the prebuilt-images feature.

prebuilt-images stories US-5 (AC-5.1 .. AC-5.4) and AC-4.2 (no literal registry
credential). ``docker-compose.yml`` no longer builds, so every documented
command that builds the platform images must go through
``docker-compose.build.yml`` (``COMPOSE_FILE`` or an explicit ``-f``). Reading
text only: no Docker, no network.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_README = _ROOT / "README.md"
_QUICKSTART = _ROOT / "docs" / "quickstart.md"
_STACK = _ROOT / "docs" / "stack-commands.md"
_OVERLAY = "docker-compose.build.yml"
_DOCS = (_README, _QUICKSTART, _STACK)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── AC-5.2 ──────────────────────────────────────────────────────────────────


def test_quickstart_adds_compose_file_between_env_copy_and_first_up() -> None:
    text = _text(_QUICKSTART)
    section = text.index("### 2.1")
    copy = text.index("cp .env.example .env", section)
    line = text.index(f"COMPOSE_FILE=docker-compose.yml:{_OVERLAY}", section)
    first_up = text.index("docker compose up -d", section)
    assert copy < line < first_up


def test_readme_quick_start_mentions_the_overlay_before_up() -> None:
    text = _text(_README)
    start = text.index("cp .env.example .env")
    assert text.index(_OVERLAY, start) < text.index("docker compose up -d", start)


# ── AC-5.3 ──────────────────────────────────────────────────────────────────


def test_stack_commands_documents_overlay_three_file_form_and_release_rows() -> None:
    text = _text(_STACK)
    assert f"COMPOSE_FILE=docker-compose.yml:docker-compose.wsl-gpu.yml:{_OVERLAY}" in text, (
        "the wsl-gpu COMPOSE_FILE paragraph must carry the three-file form"
    )
    assert "deploy/release/build-push.sh <YYYY.M.N>" in text
    assert re.search(r"git tag v<YYYY\.M\.N>.*git push origin v<YYYY\.M\.N>", text)


def test_no_documented_explicit_f_up_or_build_omits_the_overlay() -> None:
    """An explicit -f ignores COMPOSE_FILE: any `up`/`build` listing -f must add the overlay."""
    offenders: list[str] = []
    for path in _DOCS:
        for no, line in enumerate(_text(path).splitlines(), start=1):
            if "docker compose" not in line or " -f docker-compose" not in line:
                continue
            if not re.search(r"\b(up|build)\b", line):
                continue
            if _OVERLAY not in line:
                offenders.append(f"{path.name}:{no}: {line.strip()[:120]}")
    assert not offenders, offenders


# ── AC-5.4 ──────────────────────────────────────────────────────────────────


def test_modified_docs_stay_inside_an_rtl_div() -> None:
    for path in _DOCS:
        text = _text(path).rstrip()
        assert text.startswith('<div dir="rtl">'), path.name
        assert text.endswith("</div>"), path.name


def test_docs_and_workflows_carry_no_registry_credential() -> None:
    pattern = re.compile(r"dckr_pat_[A-Za-z0-9_-]+|DOCKERHUB_TOKEN\s*=\s*\S+")
    files = [*_DOCS, _ROOT / ".env.example", _ROOT / ".github" / "workflows" / "release.yml"]
    files.append(_ROOT / "deploy" / "release" / "build-push.sh")
    for path in files:
        assert not pattern.search(_text(path)), path.name


# ── AC-4.2 / NFR: nothing can echo a credential ─────────────────────────────


def test_no_shell_tracing_in_release_script_or_workflow() -> None:
    for rel in ("deploy/release/build-push.sh", ".github/workflows/release.yml"):
        text = _text(_ROOT / rel)
        assert not re.search(r"set\s+-[a-z]*x|xtrace|bash\s+-x", text), rel


def test_release_script_is_valid_bash() -> None:
    done = subprocess.run(
        ["bash", "-n", str(_ROOT / "deploy" / "release" / "build-push.sh")],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert done.returncode == 0, done.stderr
