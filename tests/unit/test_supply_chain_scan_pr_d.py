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
    ".dockerignore",
    "services/ele_parse/Dockerfile",
    "services/ele_parse/requirements.txt",
    "deploy/supply-chain/pip-bootstrap.txt",
    "deploy/supply-chain/scan.sh",
    "deploy/supply-chain/license-policy.toml",
)

# The pin as shipped. Every value below is derived from the files, so a deliberate pip bump
# changes the pin and not the tests; the floor (26.2.2) is the one number written here.
PIP = base._pip_pins()["deploy/supply-chain/pip-bootstrap.txt"]
_PIP_RE = {
    "ARG": re.compile(r"^(ARG PIP_VERSION=)\S+$", re.MULTILINE),
    "REQ": re.compile(r"^(pip==)\S+( \\)$", re.MULTILINE),
}


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


def _sub(path: Path, pattern: str, repl: str) -> str:
    """Regex-replace the first match; return the matched text."""
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"{pattern!r} not in {path}"
    path.write_text(text[: match.start()] + repl + text[match.end() :], encoding="utf-8")
    return match.group(0)


def _set_pip(tree: Path, name: str, version: str) -> None:
    kind = "ARG" if name.endswith("Dockerfile") else "REQ"
    path = tree / name
    text = path.read_text(encoding="utf-8")
    new, count = _PIP_RE[kind].subn(
        lambda m: f"{m.group(1)}{version}{m.group(2) if kind == 'REQ' else ''}", text
    )
    assert count == 1, (name, count)
    path.write_text(new, encoding="utf-8")


def _bump(version: str) -> str:
    """A version that differs from `version` and is far above the floor."""
    return f"99.{version}"


def test_the_guards_pass_on_an_unmutated_copy(tree: Path) -> None:
    base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_known_advisory()
    base.test_the_app_dockerfile_installs_the_hashed_pip_before_the_project()
    base.test_the_app_runtime_stage_removes_the_base_images_own_pip()
    base.test_scan_sh_installs_the_hashed_pip_before_the_project()
    base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()
    base.test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes()


@pytest.mark.parametrize("name", base._PIP_PIN_FILES)
def test_a_pip_pin_that_differs_in_any_one_of_the_places_fails(tree: Path, name: str) -> None:
    _set_pip(tree, name, _bump(PIP))
    with pytest.raises(AssertionError, match=r"exactly|\{"):
        base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_known_advisory()


def _all_pins(tree: Path, version: str) -> None:
    for name in base._PIP_PIN_FILES:
        _set_pip(tree, name, version)


def test_a_pip_pin_that_no_longer_fixes_the_six_advisories_fails_even_when_uniform(
    tree: Path,
) -> None:
    _all_pins(tree, "26.1.2")
    with pytest.raises(AssertionError, match="PYSEC-2026-3721"):
        base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_known_advisory()


def test_the_pip_floor_is_the_certifi_fix_not_the_last_pip_audit_advisory(tree: Path) -> None:
    """26.2 and 26.2.1 fix all six pip-audit advisories and vendor the emSign-trusting certifi."""
    for version in ("26.2", "26.2.1"):
        _all_pins(tree, version)
        with pytest.raises(AssertionError, match="GHSA-6q48-6c7w-9gw2"):
            base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_known_advisory()
    _all_pins(tree, "26.2.2")
    base.test_the_pip_pin_is_one_version_everywhere_and_fixes_every_known_advisory()


def test_the_shipped_pip_pin_is_at_or_above_the_certifi_floor() -> None:
    assert tuple(int(p) for p in PIP.split(".")) >= (26, 2, 2), PIP


def test_the_pip_bootstrap_file_holds_only_the_hashed_pip_pin() -> None:
    lines = [
        ln
        for ln in (_ROOT / "deploy/supply-chain/pip-bootstrap.txt").read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert len(lines) == 2, lines
    assert re.fullmatch(r"pip==\S+ \\", lines[0]), lines
    assert re.fullmatch(r"\s+--hash=sha256:[0-9a-f]{64}", lines[1]), lines


def test_an_app_pip_bootstrap_without_require_hashes_fails(tree: Path) -> None:
    _edit(
        tree / "Dockerfile",
        "pip install --no-cache-dir --require-hashes -r pip-bootstrap.txt",
        "pip install --no-cache-dir -r pip-bootstrap.txt",
    )
    with pytest.raises(AssertionError):
        base.test_the_app_dockerfile_installs_the_hashed_pip_before_the_project()


def test_an_app_pip_bootstrap_that_the_build_context_drops_fails(tree: Path) -> None:
    path = tree / ".dockerignore"
    path.write_text(path.read_text(encoding="utf-8") + "\ndeploy/supply-chain/\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        base.test_the_app_dockerfile_installs_the_hashed_pip_before_the_project()


def test_scan_sh_without_the_hashed_pip_bootstrap_fails(tree: Path) -> None:
    _edit(
        tree / "deploy/supply-chain/scan.sh",
        "--require-hashes \\\n  -r deploy/supply-chain/pip-bootstrap.txt",
        "-r deploy/supply-chain/pip-bootstrap.txt",
    )
    with pytest.raises(AssertionError):
        base.test_scan_sh_installs_the_hashed_pip_before_the_project()


def test_an_app_runtime_stage_that_keeps_the_base_images_pip_fails(tree: Path) -> None:
    text = (tree / "Dockerfile").read_text(encoding="utf-8")
    cut = re.sub(r"^RUN rm -rf (?:.*\\\n)*.*\n", "", text, count=1, flags=re.MULTILINE)
    assert cut != text
    (tree / "Dockerfile").write_text(cut, encoding="utf-8")
    with pytest.raises(AssertionError, match="pip-removal"):
        base.test_the_app_runtime_stage_removes_the_base_images_own_pip()


@pytest.mark.parametrize(
    "dropped",
    [
        "/usr/local/bin/pip",
        "/usr/local/bin/pip3",
        "/usr/local/bin/pip3.12",
        "/usr/local/lib/python3.12/site-packages/pip",
        "/usr/local/lib/python3.12/site-packages/pip-*.dist-info",
    ],
)
def test_a_pip_removal_that_misses_one_of_its_targets_fails(tree: Path, dropped: str) -> None:
    text = (tree / "Dockerfile").read_text(encoding="utf-8")
    start = text.index("RUN rm -rf /usr/local/bin/pip")
    end = text.index("\n\n", start)
    step = text[start:end]
    token = rf"(?<!\S){re.escape(dropped)}(?!\S)"
    mutated, count = re.subn(token, "/nonexistent", step)
    assert count == 1, (dropped, step)
    (tree / "Dockerfile").write_text(text[:start] + mutated + text[end:], encoding="utf-8")
    with pytest.raises(AssertionError):
        base.test_the_app_runtime_stage_removes_the_base_images_own_pip()


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
    _sub(
        tree / "services/ele_parse/requirements.txt",
        r"--hash=sha256:[0-9a-f]{64}",
        "--hash=sha256:XYZ",
    )
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line()


def test_a_range_instead_of_an_exact_pin_fails(tree: Path) -> None:
    _edit(tree / "services/ele_parse/requirements.txt", "pymupdf==", "pymupdf>=")
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
    _sub(tree / "services/ele_parse/Dockerfile", r"@sha256:[0-9a-f]{64}", "@sha256:935c3c5b")
    with pytest.raises(AssertionError):
        base.test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes()


def test_the_shipped_files_say_the_hash_check_covers_pip_itself() -> None:
    """The pip upgrade is in the SAME hash-checked install, not a bare `pip install pip`."""
    requirements = (_ROOT / "services/ele_parse/requirements.txt").read_text(encoding="utf-8")
    assert re.search(
        rf"^pip=={re.escape(PIP)} \\\n\s+--hash=sha256:[0-9a-f]{{64}}$", requirements, re.MULTILINE
    )
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
    if [[ "$venv" == runtime && -n "${FAKE_RUNTIME_INSTALL_FAIL:-}" ]]; then
      echo "ERROR: could not install" >&2
      exit 1
    fi
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
        components = [{**base._component(n, [base._id(lic)]), "version": v} for n, v, lic in pairs]
        return dict(base._sbom(*components))

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
            ("pip", PIP, "MIT"),
            ("pymupdf", "1.28.3", "AGPL-3.0-only"),
        )
        self.ele_pkgs: tuple[tuple[str, str, str], ...] = (
            ("pip", PIP, "MIT"),
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
            "deploy/supply-chain/pip-bootstrap.txt",
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


def _sections(report: str, scan: _Scan) -> list[tuple[str, str]]:
    """(text, pymupdf version) of the runtime report and of the ele-parse report."""
    runtime_at = report.index("# Supply chain report -- runtime (app image)")
    ele_at = report.index("# Supply chain report -- ele-parse image")
    freeze_at = report.index("## ele-parse freeze")
    versions = (
        {n: v for n, v, _ in scan.runtime_pkgs}["pymupdf"],
        {n: v for n, v, _ in scan.ele_pkgs}["pymupdf"],
    )
    return [
        (report[runtime_at:ele_at], versions[0]),
        (report[ele_at:freeze_at], versions[1]),
    ]


def test_both_environments_clean_exit_zero_and_the_report_holds_both(scan: _Scan) -> None:
    proc = scan.run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _summary(proc, "runtime (app image)").endswith("PASS")
    assert _summary(proc, "ele-parse image").endswith("PASS")
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert "# Supply chain report -- runtime (app image)" in report
    assert "# Supply chain report -- ele-parse image" in report
    assert report.index("runtime (app image)") < report.index("ele-parse image")
    # AGPL is flagged in BOTH environments, each with its OWN pymupdf version, and nothing is
    # excepted anywhere. (The "AGPL-flagged" header is printed even when nothing is flagged, so
    # counting the word proves nothing: the component line must be under the header.)
    for section, version in _sections(report, scan):
        assert f"- pymupdf {version}: AGPL-3.0-only\n" in section, section
        assert "AGPL-flagged components" in section
        assert "- none" not in section.split("| Component |")[0], section
    for label in ("runtime (app image)", "ele-parse image"):
        assert _summary(proc, label).endswith("0 excepted -> PASS")
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
    assert len(runtime) == 2, runtime
    assert "--require-hashes" in runtime[0], runtime
    assert "-r deploy/supply-chain/pip-bootstrap.txt" in runtime[0], runtime
    assert ".[parsers]" in runtime[1], runtime


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
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert f"# Supply chain report -- {failing}\n\n**Result: FAIL**" in report  # it ran
    assert "NOT RUN" not in report
    assert f"# Supply chain report -- {passing}\n" in report


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
    scan.ele_pkgs = (("pip", PIP, "MIT"), ("pymupdf", "1.28.2", "Proprietary"))
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
    assert f"# Supply chain report -- {failing}\n\n**Result: NOT RUN**" in report
    other = "runtime (app image)" if failing == "ele-parse image" else "ele-parse image"
    assert _summary(proc, other).endswith("PASS")
    assert report.count("NOT RUN") == 1


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
        ("sbom.cdx.json", {"FAKE_RUNTIME_SBOM": "skip"}),
        ("ele-parse/audit.json", {"FAKE_ELE_AUDIT": "crash"}),
        ("ele-parse/sbom.cdx.json", {"FAKE_ELE_SBOM": "skip"}),
    ],
)
def test_a_stale_output_of_an_earlier_run_is_never_read_as_fresh(
    scan: _Scan, stale: str, overrides: dict[str, str]
) -> None:
    """The stale file is a COMPLETE, passing one: only the cleanup in scan.sh keeps it unread.

    Mutation-checked: with the `rm` lines of scan.sh removed the exit code is 0 here.
    """
    ele = stale.startswith("ele-parse/")
    pkgs = scan.ele_pkgs if ele else scan.runtime_pkgs
    complete = (
        scan._audit(*((n, v) for n, v, _ in pkgs))
        if stale.endswith("audit.json")
        else scan._sbom(*pkgs)
    )
    (scan.out / stale).parent.mkdir(parents=True, exist_ok=True)
    (scan.out / stale).write_text(json.dumps(complete), encoding="utf-8")
    proc = scan.run(**overrides)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "NOT RUN" in (scan.out / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("stale", ["report.md", "sbom.cdx.json", "runtime-freeze.txt"])
def test_an_aborted_run_leaves_no_output_of_an_earlier_run(scan: _Scan, stale: str) -> None:
    """QA-1: a run that dies before it writes anything must not leave a PASS report behind."""
    scan.out.mkdir(parents=True)
    (scan.out / stale).write_text("PASS from an earlier run\n", encoding="utf-8")
    proc = scan.run(FAKE_RUNTIME_INSTALL_FAIL="1")
    assert proc.returncode != 0
    assert not (scan.out / stale).exists(), "stale evidence survived an aborted run"


def test_the_report_header_prints_the_frozen_pip_not_the_pin(scan: _Scan) -> None:
    scan.ele_pkgs = (("pip", "25.0.1", "MIT"), ("pymupdf", "1.28.2", "AGPL-3.0-only"))
    proc = scan.run()
    report = (scan.out / "report.md").read_text(encoding="utf-8")
    assert "freeze.txt`, pip 25.0.1, installed with --require-hashes" in report, proc.stdout
    assert f"runtime-freeze.txt`, pip {PIP}\n" in report


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
    scan.runtime_pkgs = (("pip", PIP, "MIT"), ("pymupdf", "1.28.2", "AGPL-3.0-only"))
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
