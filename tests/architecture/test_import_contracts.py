"""Architecture gate: the import-linter contracts must hold (09 §4, D-17).

Runs ``lint-imports`` against the root ``.importlinter`` and fails the build on
any layering/boundary violation. Fast (static, no I/O) so it runs early in CI.

Beyond "the run passes" it pins the shape of the contract file itself (PLT-16,
INC0-AC-7): how many contracts there are, which ids, that the WireTwin section
contract keeps its form, and that nobody quietly grows an ``ignore_imports``.
"""

from __future__ import annotations

import configparser
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".importlinter"
ELECTRICAL = REPO_ROOT / "src" / "app" / "modules" / "electrical"

_PREFIX = "importlinter:contract:"
_EXPECTED_CONTRACTS = {
    "layers",
    "domain-purity",
    "application-boundaries",
    "modules-independent",
    "agents-independent",
    "agents-no-api-no-infra",
    "infra-only-via-composition-root",
    "framework-kernel",
    "electrical-contexts",
}
# Every sanctioned hole, by contract. A new entry needs human approval (CLAUDE.md).
_EXPECTED_IGNORES = {
    "layers": {
        "app.framework.di.composition_root -> app.modules.**",
        "app.framework.di.composition_root -> app.agents.orchestrator",
    },
    "infra-only-via-composition-root": {
        "app.framework.di.composition_root -> app.infrastructure.**",
        "app.framework.di.storage_binding -> app.infrastructure.**",
        "app.framework.di.vault_binding -> app.infrastructure.**",
    },
    "framework-kernel": {
        "app.framework.di.composition_root -> app.modules.**",
        "app.framework.di.composition_root -> app.agents.orchestrator",
    },
}
_SECTIONS = ("drawings", "circuits", "search", "diagnostics")
_LAYERS = ("domain", "application", "ports", "adapters")


def _lint_imports_exe() -> str:
    """The ``lint-imports`` installed beside the running interpreter.

    ``shutil.which`` only sees PATH, which is empty of the venv when pytest is
    run as ``.venv/bin/pytest`` without activating it (INC0-D-12).
    """
    exe = Path(sys.executable).parent / "lint-imports"
    if not exe.exists():
        pytest.fail(f"lint-imports not found beside the interpreter: {exe}")
    return str(exe)


def _parse() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(CONFIG, encoding="utf-8")
    return parser


def _contracts() -> dict[str, configparser.SectionProxy]:
    parser = _parse()
    return {
        name.removeprefix(_PREFIX): parser[name]
        for name in parser.sections()
        if name.startswith(_PREFIX)
    }


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def test_import_contracts() -> None:
    result = subprocess.run(
        [_lint_imports_exe()],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"import-linter failed:\n{result.stdout}\n{result.stderr}"
    assert "Contracts: 9 kept, 0 broken." in result.stdout


def test_the_contract_set_is_the_nine_documented() -> None:
    assert set(_contracts()) == _EXPECTED_CONTRACTS


def test_ignore_imports_are_exactly_the_sanctioned_ones() -> None:
    found = {
        name: set(_lines(section["ignore_imports"]))
        for name, section in _contracts().items()
        if "ignore_imports" in section
    }
    assert found == _EXPECTED_IGNORES


def test_electrical_contexts_keeps_its_shape() -> None:
    section = _contracts()["electrical-contexts"]
    assert section["type"] == "layers"
    assert _lines(section["containers"]) == ["app.modules.electrical"]
    assert _lines(section["layers"]) == ["drawings | circuits | search | diagnostics", "shared"]
    assert section["exhaustive"] == "true"


def test_the_widened_contracts_reach_the_electrical_sections() -> None:
    contracts = _contracts()
    assert {"app.modules.electrical.*.domain", "app.modules.electrical.shared"} <= set(
        _lines(contracts["domain-purity"]["source_modules"])
    )
    assert "app.modules.electrical.*.application" in _lines(
        contracts["application-boundaries"]["source_modules"]
    )
    assert "app.modules.electrical" in _lines(contracts["modules-independent"]["modules"])


def test_the_skeleton_is_22_empty_packages() -> None:
    inits = sorted(ELECTRICAL.rglob("__init__.py"))
    assert len(inits) == 22
    # Nothing but the five packages directly under the container (`exhaustive`).
    assert sorted(p.name for p in ELECTRICAL.iterdir() if p.name != "__pycache__") == sorted(
        ["__init__.py", "shared", *_SECTIONS]
    )
    for section in _SECTIONS:
        for layer in _LAYERS:
            assert (ELECTRICAL / section / layer / "__init__.py").is_file()
    for init in inits:
        # One docstring line, no imports: the module stays empty until INC-1.
        body = [ln for ln in init.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(body) == 1 and body[0].startswith('"""'), init


# --------------------------------------------------------------------------- #
# The count in the live documents (PLT-23, INC0-AC-8)                         #
# --------------------------------------------------------------------------- #
# `CLAUDE.md` is deliberately absent: it is edited by the owner at merge
# (INC0-Q-7), and dated records under `docs/delivery/` are never rewritten.
_NINE = {
    "docs/architecture.md": "تسعةُ عقودٍ في `.importlinter`",
    "docs/architecture.html": "تسعةُ عقودٍ في <code>.importlinter</code>",
    "docs/quickstart.md": "9 عقود",
    "docs/stack-commands.md": "تسعة عقود",
    "docs/design/09-testing-strategy.md": "استقلال أقسام `electrical`",
}
_STALE = (
    "8 عقود",
    "ثمانية عقود",
    "ثمانيةُ عقود",
    "ثمانيةُ عقودٍ",
    "العقودُ الثمانية",
    "العقود الثمانية",
    "8 contracts",
    "8 kept",
)


@pytest.mark.parametrize("path", sorted(_NINE))
def test_the_live_docs_state_nine_contracts_and_no_stale_count(path: str) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert _NINE[path] in text, f"{path} does not state the nine-contract count"
    stale = [phrase for phrase in _STALE if phrase in text]
    assert not stale, f"{path} still carries a stale contract count: {stale}"
