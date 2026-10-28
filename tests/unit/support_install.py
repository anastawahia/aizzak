"""Test harness for `deploy/release/install.sh` and `bundle.sh` (install-bundle, design.md 5.1).

Nothing here touches Docker, a network or the real `.env`. The scripts run in a temporary tree with
fake `docker`, `curl` and `df` first on PATH. Every fake call is appended to `calls.log` with its
argv, cwd, its whole environment and the sha256 of what came on stdin (never the stdin itself), so a
test can assert both the ORDER of calls and that no secret travelled in an argument or a variable.

The fake `docker` is a small state machine in a JSON file. It reads the real Compose files (as a
model written once per session) and the `.env` of the tree, so what it reports (services, images,
config hashes) follows the same inputs Compose would use. Its hash model is the one design.md 1
measured: sha256(service + .env), with every VAULT_SECRET_ID line dropped for the services that do
not carry the key.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import io
import json
import marshal
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tests.unit.support_compose import COMPOSE, GPU_OVERLAY

REPO_ROOT = Path(__file__).resolve().parents[2]
LIST_FILE = REPO_ROOT / "deploy" / "release" / "bundle-files.txt"
INSTALL_SH = REPO_ROOT / "deploy" / "release" / "install.sh"
FAKE_SOURCE = Path(__file__).resolve().parent / "support_install_fake.py"
BUNDLE_SH = REPO_ROOT / "deploy" / "release" / "bundle.sh"

VERSION = "2026.10.99"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PREFIXES = ("app", "embedding", "embedding-cuda")
IMAGE_REPO = "anastawahia/aizzak"
SECRET_ID = "5b2a1c9e-4d3f-4a6b-8c7d-0e1f2a3b4c5d"
VAULT_TOKEN = "hvs.PLANTEDTOKENFORTHELEAKCHECK0123456789"
CARRIERS = (
    "app",
    "migrate",
    "ops-scheduler",
    "outbox-relay",
    "wal-shipper",
    "worker-knowledge",
    "worker-media",
    "worker-memory",
    "backup",
)

# The 13 passwords FR-12 names, in the template's order.
SECRET_KEYS = (
    "POSTGRES_SUPERUSER_PASSWORD",
    "AIZZAK_OWNER_PASSWORD",
    "APP_RW_PASSWORD",
    "OUTBOX_RELAY_PASSWORD",
    "RETENTION_SWEEPER_PASSWORD",
    "METRICS_READER_PASSWORD",
    "TRANSIT_ROTATOR_PASSWORD",
    "WORKSPACE_PURGER_PASSWORD",
    "BACKUP_OPERATOR_PASSWORD",
    "METRICS_EXPORTER_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
)


def digest_of(prefix: str, version: str = VERSION) -> str:
    return "sha256:" + hashlib.sha256(f"{prefix}-{version}".encode()).hexdigest()


def version_text(version: str = VERSION, commit: str = COMMIT) -> str:
    lines = [version, f"commit {commit}"]
    lines += [f"{IMAGE_REPO}:{p}-{version}@{digest_of(p, version)}" for p in PREFIXES]
    return "\n".join(lines) + "\n"


def listed_paths() -> list[str]:
    paths = []
    for raw in LIST_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            paths.append(line)
    return paths


def tracked_files(paths: list[str]) -> list[str]:
    done = subprocess.run(
        ["git", "ls-files", "--", *[p.rstrip("/") for p in paths]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.split()


def bundle_tree(tmp_path: Path, version: str = VERSION) -> Path:
    """An unpacked bundle, built without bundle.sh (for speed): the listed files from the working
    tree, install.sh at the root, and a VERSION with fake but well-formed digests."""
    root = tmp_path / f"aizzak-{version}"
    for rel in tracked_files(listed_paths()):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    shutil.copy2(INSTALL_SH, root / "install.sh")
    (root / "install.sh").chmod(0o755)
    (root / "VERSION").write_text(version_text(version), encoding="utf-8")
    return root


# ── a git repository to run bundle.sh in ────────────────────────────────────

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "2026-10-19T12:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-19T12:00:00Z",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **_GIT_ENV},
    )
    return done.stdout.strip()


# Tracked on purpose, never listed: if one of these ends up in a bundle the content check failed.
TRACKED_TRAPS = (
    "src/app/x.py",
    "tests/x.py",
    "deploy/load/tokens.json",
    "docker-compose.build.yml",
)
# Untracked in the working directory: a bundle built from the tag must never see them.
UNTRACKED_TRAPS = (
    ".env",
    "deploy/nginx/certs/server.key",
    "deploy/postgres/sizes/auto.conf",
    "init.json",
    ".auto-size-tmp-x",
)


def release_repo(tmp_path: Path, tag: str = f"v{VERSION}", *, extra_list: str = "") -> Path:
    """A temporary git repository holding the listed files and deploy/release/, with the traps
    above, committed and tagged. `extra_list` appends lines to the committed bundle-files.txt."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    for rel in tracked_files(listed_paths()):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    release = repo / "deploy" / "release"
    release.mkdir(parents=True, exist_ok=True)
    for source in (REPO_ROOT / "deploy" / "release").iterdir():
        if source.is_file():
            shutil.copy2(source, release / source.name)
    if extra_list:
        with (release / "bundle-files.txt").open("a", encoding="utf-8") as handle:
            handle.write(extra_list)
    for rel in TRACKED_TRAPS:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("trap\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release")
    git(repo, "tag", tag)
    for rel in UNTRACKED_TRAPS:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("PLANTED-SECRET\n", encoding="utf-8")
    with (repo / "docker-compose.yml").open("a", encoding="utf-8") as handle:
        handle.write("# LOCAL EDIT never committed\n")
    return repo


def digests_file(repo: Path, tag: str = f"v{VERSION}", version: str = VERSION) -> Path:
    commit = git(repo, "rev-parse", f"{tag}^{{commit}}")
    lines = [f"{IMAGE_REPO}:{p}-{version} {digest_of(p, version)} {commit}" for p in PREFIXES]
    path = repo.parent / "release-digests.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_bundle(repo: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "deploy" / "release" / "bundle.sh"), *args],
        cwd=cwd or repo,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(repo.parent), **_GIT_ENV},
        check=False,
    )


# ── the model of the Compose files the fake docker reads ────────────────────


@functools.cache
def compose_model() -> dict[str, dict[str, Any]]:
    """service -> profiles, depends_on, restart, image, carrier, for each file set."""
    model: dict[str, dict[str, Any]] = {}
    for mode, files in (("cpu", (COMPOSE,)), ("gpu", (COMPOSE, GPU_OVERLAY))):
        raw = [yaml.safe_load(f.read_text(encoding="utf-8")) for f in files]
        merged: dict[str, Any] = {}
        for doc in raw:
            for name, body in (doc.get("services") or {}).items():
                target = merged.setdefault(name, {})
                for key, value in (body or {}).items():
                    old = target.get(key)
                    if isinstance(old, dict) and isinstance(value, dict):
                        target[key] = {**old, **value}
                    else:
                        target[key] = value
        services: dict[str, Any] = {}
        for name, body in merged.items():
            depends = body.get("depends_on") or []
            services[name] = {
                "profiles": body.get("profiles") or [],
                "depends_on": list(depends),
                "restart": str(body.get("restart", "no")),
                "image": body.get("image"),
                "carrier": "VAULT_SECRET_ID" in json.dumps(body.get("environment") or {}),
                "vars": sorted(set(re.findall(r"\$\{([A-Z0-9_]+)", json.dumps(body)))),
            }
        model[mode] = services
    return model


@dataclass
class Harness:
    """One fake machine: where its files live and how to run the scripts on it."""

    root: Path  # the unpacked bundle
    fake: Path  # state.bin, calls.log, model.bin, bin/
    sysctl: Path
    sysctl_d: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def calls_path(self) -> Path:
        return self.fake / "calls.log"

    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_path.exists():
            return []
        return [ast.literal_eval(line) for line in self.calls_path.read_text().splitlines()]

    def tool_calls(self, tool: str) -> list[dict[str, Any]]:
        path = self.fake / "tools.log"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        return [r for r in rows if r["tool"] == tool]

    def docker_calls_since(self, mark: int) -> list[list[str]]:
        return [c["argv"] for c in self.calls()[mark:] if c["tool"] == "docker"]

    def docker_calls(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls() if c["tool"] == "docker"]

    def state(self) -> dict[str, Any]:
        path = self.fake / "state.bin"
        return marshal.loads(path.read_bytes()) if path.exists() else {}


def make_harness(tmp_path: Path, version: str = VERSION, *, preflight_rc: int = 0) -> Harness:
    root = bundle_tree(tmp_path, version)
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    bin_dir.mkdir(parents=True)
    (fake / "model.bin").write_bytes(marshal.dumps(compose_model()))
    source = FAKE_SOURCE.read_text(encoding="utf-8")
    for name in ("docker", "curl", "df"):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable} -IS\n" + source, encoding="utf-8")
        path.chmod(0o755)
    (bin_dir / "python3").symlink_to(sys.executable)
    sysctl = tmp_path / "sysctl"
    (sysctl / "vm").mkdir(parents=True)
    (sysctl / "vm" / "overcommit_memory").write_text("0\n", encoding="utf-8")
    sysctl_d = tmp_path / "sysctl.d"
    sysctl_d.mkdir()
    set_preflight(root, preflight_rc)
    wrap_host(root)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SYSCTL_ROOT": str(sysctl),
        "SYSCTL_D_DIR": str(sysctl_d),
        "AIZZAK_TOOLS_LOG": str(fake / "tools.log"),
    }
    return Harness(root=root, fake=fake, sysctl=sysctl, sysctl_d=sysctl_d, env=env)


def set_preflight(root: Path, rc: int) -> None:
    script = root / "deploy" / "gpu-preflight.sh"
    script.write_text(f"#!/usr/bin/env bash\necho 'fake preflight'\nexit {rc}\n", encoding="utf-8")
    script.chmod(0o755)


_WRAP_AUTO = """
import json, os, runpy, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.environ.get("AIZZAK_TOOLS_LOG", os.devnull), "a") as log:
    log.write(json.dumps({"tool": "auto_size", "argv": sys.argv[1:], "isolated": sys.flags.isolated,
                          "cwd": os.getcwd(), "env": dict(os.environ)}) + "\\n")
sys.argv = [sys.argv[0], *sys.argv[1:], "--host-cpus", "16", "--host-memory-gb", "32"]
runpy.run_path(os.path.join(here, "auto_size_real.py"), run_name="__main__")
"""

_WRAP_BUDGET = """#!/usr/bin/env bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -c '
import json, os, sys
with open(os.environ.get("AIZZAK_TOOLS_LOG", os.devnull), "a") as log:
    record = {"tool": "resource-budget", "argv": sys.argv[1:], "env": dict(os.environ)}
    log.write(json.dumps(record) + chr(10))
' "$@"
exec bash "$here/resource-budget-real.sh" "$@" --host-cpus 16 --host-memory-gb 32
"""


def wrap_host(root: Path) -> None:
    """Keep the real generator and the real budget script, but make both judge a 16 vCPU / 32 GB
    host, so a result never depends on the machine the tests run on. Both log their calls."""
    deploy = root / "deploy"
    shutil.copy2(deploy / "auto_size.py", deploy / "auto_size_real.py")
    (deploy / "auto_size.py").write_text(_WRAP_AUTO, encoding="utf-8")
    shutil.copy2(deploy / "resource-budget.sh", deploy / "resource-budget-real.sh")
    (deploy / "resource-budget.sh").write_text(_WRAP_BUDGET, encoding="utf-8")
    for name in ("auto_size.py", "resource-budget.sh"):
        (deploy / name).chmod(0o755)


def stub_tool(root: Path, rel: str, rc: int, log: Path | None = None) -> None:
    """Replace a tool in the tree with one that records its environment and exits with `rc`."""
    path = root / rel
    if rel.endswith(".py"):
        body = (
            "import json, os, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps({{'tool': {rel!r}, 'argv': sys.argv[1:],"
            " 'isolated': sys.flags.isolated, 'env': dict(os.environ)}) + '\\n')\n"
            f"print('stub {rel}')\nsys.exit({rc})\n"
        )
    else:
        body = (
            "#!/usr/bin/env bash\n"
            f"python3 -c \"import json,os,sys;open({str(log)!r},'a').write(json.dumps("
            f"{{'tool':{rel!r},'argv':sys.argv[1:],'env':dict(os.environ)}})+chr(10))\" \"$@\"\n"
            f"echo 'stub {rel}'\nexit {rc}\n"
        )
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def snapshot(root: Path) -> dict[str, str]:
    """path -> sha256 of every file under root: the 'nothing was written' evidence."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


FLOW_ARGS = ("--domain", "example.com", "--firebase-project", "demo-proj")


def run_entry(
    h: Harness,
    *args: str,
    env: dict[str, str] | None = None,
    umask: int = 0o022,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run ./install.sh itself (the pre-root paths: it stops at 4 on a non-root test machine)."""
    return subprocess.run(
        ["bash", "-c", f'umask {umask:03o}; exec bash ./install.sh "$@"', "_", *args],
        cwd=h.root,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env={**h.env, **(env or {})},
        timeout=timeout,
        check=False,
    )


_STEP_SHIM = """
umask {umask}
source "$1"
shift
{overrides}
prepare "$@" && run_steps
"""


def run_steps(
    h: Harness,
    *args: str,
    env: dict[str, str] | None = None,
    overrides: str = "POLL_S=0.01 BOOT_DEADLINE_S=20 HEALTH_DEADLINE_S=1",
    umask: int = 0o022,
    timeout: int = 180,
    script: str = "install.sh",
) -> subprocess.CompletedProcess[str]:
    """Source install.sh and run `prepare` then `run_steps`, skipping only require_root /
    require_trusted / lock_bundle (a test machine is not root)."""
    shim = _STEP_SHIM.format(umask=f"{umask:03o}", overrides=overrides)
    return subprocess.run(
        ["bash", "-c", shim, "_", str(h.root / script), *args],
        cwd=h.root,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env={**h.env, **(env or {})},
        timeout=timeout,
        check=False,
    )


def run_function(
    h: Harness,
    code: str,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source install.sh and run `code` (a few shell lines) in it, with `args` as $1...: for single
    functions. `set -e` is on, as in the script."""
    return subprocess.run(
        [
            "bash",
            "-c",
            'umask 022; source "$1"; shift; ' + code,
            "_",
            str(h.root / "install.sh"),
            *args,
        ],
        cwd=h.root,
        capture_output=True,
        text=True,
        input=stdin,
        env={**h.env, **(env or {})},
        timeout=120,
        check=False,
    )


def env_tool_shell(
    h: Harness, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """`env_tool ARGS` through bash and a real interpreter, exactly as install.sh calls it."""
    return run_function(h, 'env_tool "$@"', *args, env=env)


def parse_env(text: str) -> dict[str, str]:
    """Active KEY=VALUE lines, last one wins (as Compose reads them)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        found = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if found:
            values[found.group(1)] = found.group(2).strip()
    return values


def clone_harness(h: Harness, tmp_path: Path) -> Harness:
    """A copy of a harness (tree, fake state, sysctl dirs) that can be mutated on its own."""
    old = h.fake.parent
    shutil.copytree(old, tmp_path / "clone", symlinks=True, dirs_exist_ok=True)
    new = tmp_path / "clone"
    (new / "fake" / "calls.log").unlink(missing_ok=True)  # the copy starts its own call log
    (new / "fake" / "tools.log").unlink(missing_ok=True)
    swap = {k: v.replace(str(old), str(new)) for k, v in h.env.items()}
    return Harness(
        root=new / h.root.name,
        fake=new / "fake",
        sysctl=new / "sysctl",
        sysctl_d=new / "sysctl.d",
        env=swap,
    )


_PROGRAM = re.search(
    r"env_tool\(\) \{\n  python3 -I - \"\$@\" <<'PY'\n(.*?)\nPY\n",
    INSTALL_SH.read_text(encoding="utf-8"),
    re.S,
)
assert _PROGRAM is not None
_PROGRAM_TAIL = "sys.exit(main(sys.argv[1:]))"


@functools.cache
def _env_tool_namespace() -> dict[str, Any]:
    """The program embedded in install.sh, loaded as it is (not a copy) without its last line."""
    assert _PROGRAM is not None
    source = _PROGRAM.group(1).rstrip()
    assert source.endswith(_PROGRAM_TAIL)
    namespace: dict[str, Any] = {"__name__": "env_tool_under_test"}
    exec(compile(source[: -len(_PROGRAM_TAIL)], "install.sh:env_tool", "exec"), namespace)
    return namespace


def env_tool(
    h: Harness, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """`env_tool ARGS` of install.sh, run in this process: the same program text, a hundredth of
    the cost of a shell plus an interpreter. Umask 022, as the script's callers have."""
    del h
    out, err = io.StringIO(), io.StringIO()
    keys = {"AIZZAK_INSTALL_SECRET_ID", *(env or {})}
    saved = {k: os.environ.pop(k, None) for k in keys}
    os.environ.update(env or {})
    umask = os.umask(0o022)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = _env_tool_namespace()["main"](list(args))
            except SystemExit as exc:  # a bad option: the script's interpreter would print it
                print(exc.code, file=err)
                rc = 1
    finally:
        os.umask(umask)
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
    return subprocess.CompletedProcess(list(args), rc, out.getvalue(), err.getvalue())
