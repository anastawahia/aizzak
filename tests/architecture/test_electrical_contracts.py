"""WireTwin's import contracts must fail when broken (PLT-16, INC0-AC-7).

A contract that has never been seen to fail proves nothing: import-linter keeps
a contract whose source matches no module without a word, and `*` matches ONE
name component. So each new or widened contract is run here, with the text of
the real ``.importlinter``, over a throwaway ``app`` tree that breaks it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".importlinter"

_MODULES = [
    "workspace",
    "access",
    "credentials",
    "conversations",
    "memory",
    "files",
    "knowledge",
    "media",
    "integrations",
    "usage",
    "spaces",
    "electrical",
]
_AGENTS = ["rag_agent", "image_agent", "video_agent", "data_analysis_agent", "file_editing_agent"]
_SECTIONS = ("drawings", "circuits", "search", "diagnostics")
_LAYERS = ("domain", "application", "ports", "adapters")


def _touch(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_tree(root: Path) -> None:
    """A clean `app` tree that every real contract keeps."""
    for pkg in ("", "framework", "infrastructure", "api", "agents", "modules"):
        _touch(root, f"app/{pkg}/__init__.py".replace("//", "/"))
    # The real file sanctions three wiring imports from the composition root; the
    # `layers` contract alerts (error) on an ignore that matches nothing.
    _touch(root, "app/framework/di/__init__.py")
    _touch(root, "app/agents/orchestrator/__init__.py")
    _touch(root, "app/infrastructure/storage.py")
    _touch(
        root,
        "app/framework/di/composition_root.py",
        "import app.agents.orchestrator\n"
        "import app.infrastructure.storage\n"
        "import app.modules.files\n",
    )
    for agent in _AGENTS:
        _touch(root, f"app/agents/{agent}/__init__.py")
    for module in _MODULES:
        _touch(root, f"app/modules/{module}/__init__.py")
    _touch(root, "app/modules/electrical/shared/__init__.py")
    for section in _SECTIONS:
        _touch(root, f"app/modules/electrical/{section}/__init__.py")
        for layer in _LAYERS:
            _touch(root, f"app/modules/electrical/{section}/{layer}/__init__.py")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    exe = Path(sys.executable).parent / "lint-imports"
    if not exe.exists():
        pytest.fail(f"lint-imports not found beside the interpreter: {exe}")
    config = root / "contracts.ini"
    config.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(root)}
    return subprocess.run(
        [str(exe), "--no-cache", "--config", str(config)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _assert_broken(root: Path, contract_name: str) -> None:
    result = _run(root)
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert f"{contract_name} BROKEN" in out, out


def test_the_clean_tree_keeps_every_contract(tmp_path: Path) -> None:
    """Guards the harness itself: a broken fixture would make every proof pass."""
    _build_tree(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Contracts: 9 kept, 0 broken." in result.stdout


def test_a_section_importing_a_section_breaks_electrical_contexts(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    _touch(
        tmp_path,
        "app/modules/electrical/drawings/domain/leak.py",
        "import app.modules.electrical.circuits.domain\n",
    )
    _assert_broken(tmp_path, "WireTwin sections are independent and shared is the floor")


def test_shared_importing_a_section_breaks_electrical_contexts(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    _touch(
        tmp_path,
        "app/modules/electrical/shared/leak.py",
        "import app.modules.electrical.search.domain\n",
    )
    _assert_broken(tmp_path, "WireTwin sections are independent and shared is the floor")


def test_an_undeclared_child_breaks_electrical_contexts(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    _touch(tmp_path, "app/modules/electrical/fifth/__init__.py")
    _assert_broken(tmp_path, "WireTwin sections are independent and shared is the floor")


def test_a_section_domain_importing_pydantic_breaks_domain_purity(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    _touch(tmp_path, "app/modules/electrical/drawings/domain/leak.py", "import pydantic\n")
    _assert_broken(tmp_path, "Domain is pure (no technical imports)")


def test_shared_importing_pydantic_breaks_domain_purity(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    _touch(tmp_path, "app/modules/electrical/shared/leak.py", "import pydantic\n")
    _assert_broken(tmp_path, "Domain is pure (no technical imports)")


def test_a_section_application_importing_fastapi_breaks_application_boundaries(
    tmp_path: Path,
) -> None:
    _build_tree(tmp_path)
    _touch(tmp_path, "app/modules/electrical/search/application/leak.py", "import fastapi\n")
    _assert_broken(tmp_path, "Application depends on domain + framework only")


def test_electrical_importing_files_breaks_modules_independent(tmp_path: Path) -> None:
    _build_tree(tmp_path)
    _touch(
        tmp_path,
        "app/modules/electrical/diagnostics/ports/leak.py",
        "import app.modules.files\n",
    )
    _assert_broken(tmp_path, "Business modules do not import each other")
