"""QA of PR-D (INC1-AC-15, INC1-AC-16): the supply-chain scan must FAIL when it should.

Two groups, both offline:

* drift: the pip pin, the single pymupdf pin, the hashes and the base-image digest are guarded
  by tests in ``test_supply_chain_check.py``. Here each guard is shown to catch a mutated copy of
  the files (a guard that cannot fail proves nothing);
* behaviour of ``deploy/supply-chain/scan.sh`` itself: it runs in a temp tree with fake
  ``python -m venv`` / ``pip`` / ``cyclonedx-py`` / ``pip-audit`` and the REAL checker and the
  REAL policy, so the exit code, the "worse of the two environments" rule, the stale-output
  cleanup and the pymupdf note are exercised without a network.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DIR = _ROOT / "deploy" / "supply-chain"
base: ModuleType = importlib.import_module("tests.unit.test_supply_chain_check")


# ── drift: every guard fails on a mutated copy ──────────────────────────────

_FILES = (
    "Dockerfile",
    "services/ele_parse/Dockerfile",
    "services/ele_parse/requirements.txt",
    "deploy/supply-chain/scan.sh",
    "deploy/supply-chain/license-policy.toml",
)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the files the guards read, with the guards pointed at it."""
    for name in _FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_ROOT / name, target)
    monkeypatch.setattr(base, "_ROOT", tmp_path)
    monkeypatch.setattr(base, "_DIR", tmp_path / "deploy" / "supply-chain")
    return tmp_path


def _edit(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def test_the_guards_pass_on_an_unmutated_copy(tree: Path) -> None:
    base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_inc0_q_10_advisory()
    base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()
    base.test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes()


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        ("Dockerfile", "ARG PIP_VERSION=26.2", "ARG PIP_VERSION=26.3"),
        ("services/ele_parse/Dockerfile", "ARG PIP_VERSION=26.2", "ARG PIP_VERSION=26.3"),
        ("deploy/supply-chain/scan.sh", "pip_version=26.2", "pip_version=26.3"),
        ("services/ele_parse/requirements.txt", "pip==26.2 \\", "pip==26.3 \\"),
    ],
)
def test_a_pip_pin_that_differs_in_any_one_of_the_four_places_fails(
    tree: Path, name: str, old: str, new: str
) -> None:
    _edit(tree / name, old, new)
    with pytest.raises(AssertionError):
        base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_inc0_q_10_advisory()


def test_a_pip_pin_that_no_longer_fixes_the_six_advisories_fails_even_when_uniform(
    tree: Path,
) -> None:
    for name, old, new in (
        ("Dockerfile", "ARG PIP_VERSION=26.2", "ARG PIP_VERSION=26.1.2"),
        ("services/ele_parse/Dockerfile", "ARG PIP_VERSION=26.2", "ARG PIP_VERSION=26.1.2"),
        ("deploy/supply-chain/scan.sh", "pip_version=26.2", "pip_version=26.1.2"),
        ("services/ele_parse/requirements.txt", "pip==26.2 \\", "pip==26.1.2 \\"),
    ):
        _edit(tree / name, old, new)
    with pytest.raises(AssertionError, match="PYSEC-2026-3721"):
        base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_inc0_q_10_advisory()


def test_a_second_pymupdf_pin_fails(tree: Path) -> None:
    path = tree / "services/ele_parse/requirements.txt"
    path.write_text(
        path.read_text(encoding="utf-8") + f"\npymupdf==1.27.0 \\\n    --hash=sha256:{'a' * 64}\n"
    )
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()


def test_a_requirement_without_a_hash_fails(tree: Path) -> None:
    path = tree / "services/ele_parse/requirements.txt"
    path.write_text(path.read_text(encoding="utf-8") + "\nidna==3.10\n")
    with pytest.raises(AssertionError, match="idna"):
        base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()


def test_a_malformed_hash_fails(tree: Path) -> None:
    _edit(
        tree / "services/ele_parse/requirements.txt", "--hash=sha256:397d6715", "--hash=sha256:XYZ"
    )
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()


def test_a_range_instead_of_an_exact_pin_fails(tree: Path) -> None:
    _edit(tree / "services/ele_parse/requirements.txt", "pymupdf==1.28.2", "pymupdf>=1.28.2")
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()


def test_a_tag_without_a_digest_in_the_ele_parse_dockerfile_fails(tree: Path) -> None:
    path = tree / "services/ele_parse/Dockerfile"
    text = re.sub(r"(FROM python:3\.12-slim-bookworm)@sha256:[0-9a-f]{64}", r"\1", path.read_text())
    path.write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes()


def test_an_install_without_require_hashes_in_the_ele_parse_dockerfile_fails(tree: Path) -> None:
    _edit(tree / "services/ele_parse/Dockerfile", "--require-hashes ", "")
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes()


def test_a_truncated_digest_fails(tree: Path) -> None:
    _edit(tree / "services/ele_parse/Dockerfile", "@sha256:935c3c5b", "@sha256:935c3c5bX")
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes()


def test_the_shipped_files_say_the_hash_check_covers_pip_itself() -> None:
    """The pip upgrade is in the SAME hash-checked install, not a bare `pip install pip`."""
    requirements = (_ROOT / "services/ele_parse/requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^pip==26\.2 \\\n\s+--hash=sha256:[0-9a-f]{64}$", requirements, re.MULTILINE)
    dockerfile = (_ROOT / "services/ele_parse/Dockerfile").read_text(encoding="utf-8")
    run = re.search(r"^RUN pip install .*(?:\n\s+&&.*)*", dockerfile, re.MULTILINE)
    assert run is not None
    assert "--require-hashes" in run.group(0)
    assert "pip --version" in run.group(0), "the build must fail when pip is not the pin"
    assert "--no-deps" not in run.group(0)


# ── behaviour of scan.sh ────────────────────────────────────────────────────

_FAKE_PYTHON = r"""#!/usr/bin/env bash
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  d="$3"; mkdir -p "$d/bin"
  for t in pip cyclonedx-py pip-audit; do cp "$FAKE_DIR/$t" "$d/bin/$t"; done
  printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$REAL_PY" >"$d/bin/python"
  chmod +x "$d"/bin/*
  exit 0
fi
exec "$REAL_PY" "$@"
"""

_FAKE_PIP = r"""#!/usr/bin/env bash
venv="$(basename "$(dirname "$(cd "$(dirname "$0")" && pwd)")")"
echo "$venv: $*" >>"$FAKE_LOG"
case "$1" in
  install)
    if [[ "$venv" == ele-parse-venv && -n "${FAKE_ELE_INSTALL_FAIL:-}" ]]; then
      echo "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE." >&2
      exit 1
    fi ;;
  freeze)
    if [[ "$venv" == ele-parse-venv ]]; then cat "$FAKE_ELE_FREEZE"
    else cat "$FAKE_RUNTIME_FREEZE"; fi ;;
esac
exit 0
"""

_FAKE_CYCLONEDX = r"""#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
if [[ "$2" == *ele-parse-venv* ]]; then mode="${FAKE_ELE_SBOM:-}"; src="$FAKE_ELE_SBOM_FILE"
else mode="${FAKE_RUNTIME_SBOM:-}"; src="$FAKE_RUNTIME_SBOM_FILE"; fi
[[ "$mode" == fail ]] && exit 1
[[ "$mode" == skip ]] && exit 0
cp "$src" "$out"
"""

_FAKE_AUDIT = r"""#!/usr/bin/env bash
req=""; out=""; prev=""
for a in "$@"; do
  [[ "$prev" == "-r" ]] && req="$a"; [[ "$prev" == "-o" ]] && out="$a"; prev="$a"
done
if [[ "$req" == *ele-parse* ]]; then mode="${FAKE_ELE_AUDIT:-}"; src="$FAKE_ELE_AUDIT_FILE"
else mode="${FAKE_RUNTIME_AUDIT:-}"; src="$FAKE_RUNTIME_AUDIT_FILE"; fi
[[ "$mode" == crash ]] && exit 1
cp "$src" "$out"
[[ "$mode" == vuln ]] && exit 1
exit 0
"""


def _fixtures() -> tuple[Callable[..., dict[str, Any]], Callable[..., dict[str, Any]]]:
    def sbom(*pairs: tuple[str, str, str]) -> dict[str, Any]:
        return dict(base._sbom(*[base._component(n, [base._id(lic)]) for n, _v, lic in pairs]))

    def audit(*pairs: tuple[str, str], vulnerable: str = "") -> dict[str, Any]:
        return dict(
            base._audit(
                *[
                    (n, v, [base._vuln("PYSEC-X-1", ["99"])] if n == vulnerable else [])
                    for n, v in pairs
                ]
            )
        )

    return sbom, audit


class _Scan:
    """One offline run of a copy of scan.sh."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.fake = root / "_fake"
        self.env: dict[str, str] = {}
        sbom, audit = _fixtures()
        self.runtime_pkgs: tuple[tuple[str, str, str], ...] = (
            ("pip", "26.2", "MIT"),
            ("pymupdf", "1.28.3", "AGPL-3.0-only"),
        )
        self.ele_pkgs: tuple[tuple[str, str, str], ...] = (
            ("pip", "26.2", "MIT"),
            ("pymupdf", "1.28.2", "AGPL-3.0-only"),
        )
        self._sbom, self._audit = sbom, audit
        self.fake.mkdir()
        for name, body in (
            ("python", _FAKE_PYTHON),
            ("pip", _FAKE_PIP),
            ("cyclonedx-py", _FAKE_CYCLONEDX),
            ("pip-audit", _FAKE_AUDIT),
        ):
            (self.fake / name).write_text(body, encoding="utf-8")
            (self.fake / name).chmod(0o755)
        for rel in (
            "deploy/supply-chain/scan.sh",
            "deploy/supply-chain/supply_chain_check.py",
            "deploy/supply-chain/license-policy.toml",
            "deploy/supply-chain/requirements.txt",
            "pyproject.toml",
            "services/ele_parse/requirements.txt",
        ):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_ROOT / rel, root / rel)

    def _write(self, name: str, content: object) -> str:
        path = self.fake / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content), encoding="utf-8")
        return str(path)

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        r_pairs = tuple((n, v) for n, v, _ in self.runtime_pkgs)
        e_pairs = tuple((n, v) for n, v, _ in self.ele_pkgs)
        env = {
            **os.environ,
            "PYTHON": str(self.fake / "python"),
            "REAL_PY": sys.executable,
            "FAKE_DIR": str(self.fake),
            "FAKE_LOG": str(self.fake / "calls.log"),
            "FAKE_RUNTIME_FREEZE": self._write(
                "rf.txt", "".join(f"{n}=={v}\n" for n, v in r_pairs)
            ),
            "FAKE_ELE_FREEZE": self._write("ef.txt", "".join(f"{n}=={v}\n" for n, v in e_pairs)),
            "FAKE_RUNTIME_SBOM_FILE": self._write("rs.json", self._sbom(*self.runtime_pkgs)),
            "FAKE_ELE_SBOM_FILE": self._write("es.json", self._sbom(*self.ele_pkgs)),
            "FAKE_RUNTIME_AUDIT_FILE": self._write(
                "ra.json", self._audit(*r_pairs, vulnerable=overrides.pop("_rvuln", ""))
            ),
            "FAKE_ELE_AUDIT_FILE": self._write(
                "ea.json", self._audit(*e_pairs, vulnerable=overrides.pop("_evuln", ""))
            ),
            **overrides,
        }
        return subprocess.run(
            ["bash", "deploy/supply-chain/scan.sh"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    @property
    def out(self) -> Path:
        return self.root / "build" / "supply-chain"


@pytest.fixture
def scan(tmp_path: Path) -> _Scan:
    return _Scan(tmp_path)


def _summary(proc: subprocess.CompletedProcess[str], label: str) -> str:
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(f"supply-chain [{label}]")]
    assert len(lines) == 1, proc.stdout
    return lines[0]


def test_both_environments_clean_exit_zero_and_the_report_holds_both(scan: _Scan) -> None:
    proc = scan.run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _summary(proc, "runtime (app image)").endswith("PASS")
    assert _summary(proc, "ele-parse image").endswith("PASS")
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert "# Supply chain report -- runtime (app image)" in report
    assert "# Supply chain report -- ele-parse image" in report
    # AGPL is flagged in BOTH environments, and no pip exception is applied anywhere.
    assert report.count("AGPL") >= 2
    assert report.index("runtime (app image)") < report.index("ele-parse image")
    assert (scan.out / "ele-parse" / "sbom.cdx.json").is_file()
    assert (scan.out / "ele-parse" / "audit.json").is_file()
    assert (scan.out / "ele-parse" / "freeze.txt").is_file()


def test_the_ele_parse_environment_is_installed_with_require_hashes_from_its_file(
    scan: _Scan,
) -> None:
    assert scan.run().returncode == 0
    calls = (scan.fake / "calls.log").read_text(encoding="utf-8").splitlines()
    ele = [c for c in calls if c.startswith("ele-parse-venv: install")]
    assert len(ele) == 1, calls
    assert "--require-hashes" in ele[0]
    assert "-r services/ele_parse/requirements.txt" in ele[0]
    runtime = [c for c in calls if c.startswith("runtime: install")]
    assert any("pip==26.2" in c for c in runtime), runtime
    assert runtime.index(next(c for c in runtime if "pip==26.2" in c)) < runtime.index(
        next(c for c in runtime if ".[parsers]" in c)
    )


@pytest.mark.parametrize(
    ("overrides", "failing", "passing"),
    [
        ({"_evuln": "pip", "FAKE_ELE_AUDIT": "vuln"}, "ele-parse image", "runtime (app image)"),
        ({"_rvuln": "pip", "FAKE_RUNTIME_AUDIT": "vuln"}, "runtime (app image)", "ele-parse image"),
    ],
)
def test_a_vulnerable_pip_in_one_environment_alone_fails_the_scan(
    scan: _Scan, overrides: dict[str, str], failing: str, passing: str
) -> None:
    proc = scan.run(**overrides)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _summary(proc, failing).endswith("FAIL")
    assert _summary(proc, passing).endswith("PASS")
    assert "scan.sh exit code: 1" in proc.stdout


def test_an_unapproved_licence_in_the_ele_parse_environment_alone_fails_the_scan(
    scan: _Scan,
) -> None:
    scan.ele_pkgs = (*scan.ele_pkgs, ("evil", "1.0", "GPL-3.0-only"))
    proc = scan.run()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _summary(proc, "ele-parse image").endswith("FAIL")
    assert _summary(proc, "runtime (app image)").endswith("PASS")


def test_pymupdf_without_a_licence_in_the_ele_parse_environment_alone_fails_the_scan(
    scan: _Scan,
) -> None:
    scan.ele_pkgs = (("pip", "26.2", "MIT"), ("pymupdf", "1.28.2", "Proprietary"))
    proc = scan.run()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _summary(proc, "ele-parse image").endswith("FAIL")


@pytest.mark.parametrize(
    ("overrides", "failing"),
    [
        ({"FAKE_ELE_AUDIT": "crash"}, "ele-parse image"),
        ({"FAKE_ELE_SBOM": "skip"}, "ele-parse image"),
        ({"FAKE_RUNTIME_AUDIT": "crash"}, "runtime (app image)"),
        ({"FAKE_RUNTIME_SBOM": "skip"}, "runtime (app image)"),
    ],
)
def test_a_missing_input_of_one_environment_is_exit_2_not_a_pass(
    scan: _Scan, overrides: dict[str, str], failing: str
) -> None:
    proc = scan.run(**overrides)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "scan.sh exit code: 2" in proc.stdout
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert "NOT RUN" in report


@pytest.mark.parametrize(
    "overrides",
    [
        {"_rvuln": "pip", "FAKE_RUNTIME_AUDIT": "vuln", "FAKE_ELE_AUDIT": "crash"},
        {"_evuln": "pip", "FAKE_ELE_AUDIT": "vuln", "FAKE_RUNTIME_AUDIT": "crash"},
    ],
)
def test_the_exit_code_is_the_worse_of_the_two_in_both_orders(
    scan: _Scan, overrides: dict[str, str]
) -> None:
    proc = scan.run(**overrides)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_a_tampered_hash_aborts_before_any_check_and_exits_nonzero(scan: _Scan) -> None:
    proc = scan.run(FAKE_ELE_INSTALL_FAIL="1")
    assert proc.returncode != 0
    assert "supply-chain [" not in proc.stdout, "no checker may run after a failed install"
    assert not (scan.out / "ele-parse" / "sbom.cdx.json").exists()


def test_a_failing_sbom_tool_aborts_and_exits_nonzero(scan: _Scan) -> None:
    proc = scan.run(FAKE_ELE_SBOM="fail")
    assert proc.returncode != 0
    assert "supply-chain [" not in proc.stdout


@pytest.mark.parametrize(
    ("stale", "overrides"),
    [
        ("audit.json", {"FAKE_RUNTIME_AUDIT": "crash"}),
        ("ele-parse/audit.json", {"FAKE_ELE_AUDIT": "crash"}),
        ("ele-parse/sbom.cdx.json", {"FAKE_ELE_SBOM": "skip"}),
    ],
)
def test_a_stale_output_of_an_earlier_run_is_never_read_as_fresh(
    scan: _Scan, stale: str, overrides: dict[str, str]
) -> None:
    passing = scan._audit(("pip", "26.2")) if stale.endswith("audit.json") else scan._sbom()
    (scan.out / stale).parent.mkdir(parents=True, exist_ok=True)
    (scan.out / stale).write_text(json.dumps(passing), encoding="utf-8")
    proc = scan.run(**overrides)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_the_pymupdf_versions_are_noted_when_they_differ_and_are_not_a_failure(
    scan: _Scan,
) -> None:
    proc = scan.run()  # fixtures: runtime 1.28.3, ele-parse 1.28.2
    assert proc.returncode == 0
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert "- runtime: `pymupdf==1.28.3`" in report
    assert "- ele-parse: `pymupdf==1.28.2`" in report
    assert "the versions DIFFER" in report


def test_the_pymupdf_note_says_so_when_the_versions_are_the_same(scan: _Scan) -> None:
    scan.runtime_pkgs = (("pip", "26.2", "MIT"), ("pymupdf", "1.28.2", "AGPL-3.0-only"))
    proc = scan.run()
    assert proc.returncode == 0
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert "The versions are the same." in report
    assert "DIFFER" not in report


def test_no_vulnerability_exception_is_left_to_hide_a_pip_advisory() -> None:
    """The real policy, the real checker: pip with an advisory is a violation, not excepted."""
    policy = base.sc.load_policy(_DIR / "license-policy.toml")
    audit = base._audit(("pip", "25.0.1", [base._vuln("PYSEC-2026-3721", ["26.2"])]))
    report = base.sc.check_vulnerabilities(audit, ["pip==25.0.1"], policy, base._TODAY)
    assert report.violations
    assert report.excepted == 0
