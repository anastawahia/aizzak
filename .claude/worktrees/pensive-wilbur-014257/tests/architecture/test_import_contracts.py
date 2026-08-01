"""Architecture gate: the import-linter contracts must hold (09 §4, D-17).

Runs ``lint-imports`` against the root ``.importlinter`` and fails the build on
any layering/boundary violation. Fast (static, no I/O) so it runs early in CI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts() -> None:
    exe = shutil.which("lint-imports")
    if exe is None:  # pragma: no cover - only when dev extras are absent
        pytest.skip("import-linter (lint-imports) is not installed")

    result = subprocess.run(
        [exe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"import-linter failed:\n{result.stdout}\n{result.stderr}"
