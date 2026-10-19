"""The release bundle may not silently go stale (install-bundle US-2, AC-2.1 ... AC-2.3, AC-6.1).

`deploy/release/bundle-files.txt` is the one list of what `aizzak-<V>.tar.gz` carries. A Compose
file that gains a `./deploy/...` bind mount the list forgot would build a bundle that boots into
"no such file" on a server (RunPod's `staging-4` was exactly that, `server-gpu-plan.md:414`).

The guard reads the two Compose files as YAML, in every service and every volume spelling, and
compares the bind sources with the list. Every comparison is shown failing in BOTH directions on
in-memory copies (`monitoring-profile/retro.md:88`): a bind nobody listed, a listed path that is no
longer needed, a forbidden path that was listed, and an exclusion that excludes nothing.
"""

from __future__ import annotations

import copy
import functools
import json
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.gpu.yml")
LIST_FILE = REPO_ROOT / "deploy" / "release" / "bundle-files.txt"

# Bind mounts the bundle leaves out ON PURPOSE, each with its reason. Anything else bound from
# ./deploy/ must be listed.
EXCLUDED: Mapping[str, str] = {
    "deploy/load": "k6 load harness, its accounts and tokens: tooling and secrets",
    "deploy/nginx/certs": "TLS key material; Docker creates the directory on the first `up`",
}
# In the bundle although no Compose file binds them: the Compose files themselves, the template,
# the sizes the installer reads, and the scripts it runs.
REQUIRED_EXTRA: frozenset[str] = frozenset(
    {
        "docker-compose.yml",
        "docker-compose.gpu.yml",
        ".env.example",
        "deploy/sizes",
        "deploy/auto_size.py",
        "deploy/gpu-preflight.sh",
        "deploy/host-tuning.sh",
        "deploy/resource-budget.sh",
        "deploy/rolling-deploy.sh",
    }
)
# The nine services that hold VAULT_SECRET_ID (measured: `grep VAULT_SECRET_ID` in the anchor
# every one of them merges). None may be created before install.sh has minted the key (FR-20).
CARRIERS = frozenset(
    {
        "app",
        "migrate",
        "ops-scheduler",
        "outbox-relay",
        "wal-shipper",
        "worker-knowledge",
        "worker-media",
        "worker-memory",
        "backup",
    }
)


def compose_texts() -> list[str]:
    return [(REPO_ROOT / name).read_text(encoding="utf-8") for name in COMPOSE_FILES]


@functools.cache
def _load(text: str) -> Any:
    """YAML of one document, parsed once: PyYAML needs ~80 ms for a Compose file. Never mutated."""
    return yaml.safe_load(text) or {}


def _services(texts: Iterable[str]) -> dict[str, dict[str, Any]]:
    return copy.deepcopy(_merged(tuple(texts)))


@functools.cache
def _merged(texts: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """The services of all files, merged the way Compose merges an overlay: lists (`volumes`)
    are extended, maps (`depends_on`, `environment`) are united, anything else is replaced."""
    merged: dict[str, dict[str, Any]] = {}
    for text in texts:
        loaded = _load(text)
        for name, body in (loaded.get("services") or {}).items():
            target = merged.setdefault(name, {})
            for key, value in (body or {}).items():
                old = target.get(key)
                if key == "volumes" and isinstance(old, list):
                    target[key] = [*old, *value]
                elif key in ("depends_on", "environment") and isinstance(old, dict):
                    target[key] = {**old, **(value if isinstance(value, dict) else {})}
                else:
                    target[key] = value
    return merged


def deploy_binds(texts: Iterable[str]) -> set[str]:
    """Every host path under ./deploy/ that any service bind-mounts, without the leading ./.

    Handles the short form (`./deploy/x:/y:ro`) and the long form (`type: bind`, `source:`). A
    variable inside such a path is an error: the guard cannot know what it resolves to."""
    found: set[str] = set()
    for name, body in _services(texts).items():
        for volume in body.get("volumes") or []:
            if isinstance(volume, str):
                source = volume.split(":", 1)[0]
            elif isinstance(volume, dict) and volume.get("type") == "bind":
                source = str(volume.get("source", ""))
            else:
                continue
            if not source.startswith("./deploy/") and not source.startswith("deploy/"):
                continue
            assert "$" not in source, f"{name}: a variable in a bind path under ./deploy/: {source}"
            found.add(source.removeprefix("./").rstrip("/"))
    return found


def read_list(text: str) -> list[str]:
    paths = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            paths.append(line.rstrip("/"))
    return paths


def check(
    binds: Iterable[str],
    listed: Iterable[str],
    excluded: Mapping[str, str],
    required_extra: Iterable[str],
) -> list[str]:
    """Problems, each naming its path. Empty means `listed == (binds - excluded) | extra`."""
    bound, shown = set(binds), set(listed)
    problems: list[str] = []
    for path in sorted(excluded):
        if path not in bound:
            problems.append(f"{path}: excluded as a bind mount, but no Compose file binds it")
        if path in shown:
            problems.append(f"{path}: is excluded on purpose ({excluded[path]}) yet is listed")
    want = (bound - set(excluded)) | set(required_extra)
    for path in sorted(want - shown):
        problems.append(f"{path}: needed by the Compose files but missing from bundle-files.txt")
    for path in sorted(shown - want - set(excluded)):
        problems.append(f"{path}: listed in bundle-files.txt but nothing needs it")
    return problems


def closure(services: Mapping[str, Mapping[str, Any]], roots: Sequence[str]) -> set[str]:
    """`roots` and everything they depend on, transitively."""
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        depends = (services.get(name) or {}).get("depends_on") or []
        todo.extend(depends if isinstance(depends, list) else list(depends))
    return seen


def carriers(services: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {
        name
        for name, body in services.items()
        if "VAULT_SECRET_ID" in json.dumps(body.get("environment") or {})
    }


def _real() -> tuple[set[str], list[str]]:
    return deploy_binds(compose_texts()), read_list(LIST_FILE.read_text(encoding="utf-8"))


# ── AC-2.1 ───────────────────────────────────────────────────────────────────


def test_every_deploy_bind_is_listed_or_excluded_with_a_reason() -> None:
    binds, listed = _real()
    assert check(binds, listed, EXCLUDED, REQUIRED_EXTRA) == []


def test_the_real_compose_files_have_the_27_binds_the_design_measured() -> None:
    binds, _ = _real()
    assert len(binds) == 27, sorted(binds)
    assert set(EXCLUDED) <= binds


def test_the_long_bind_syntax_and_variables_are_not_missed() -> None:
    long_form = yaml.safe_dump(
        {"services": {"x": {"volumes": [{"type": "bind", "source": "./deploy/new.conf"}]}}}
    )
    assert deploy_binds([long_form]) == {"deploy/new.conf"}
    named = yaml.safe_dump({"services": {"x": {"volumes": ["data:/var/lib/x"]}}})
    assert deploy_binds([named]) == set()
    with pytest.raises(AssertionError, match="variable in a bind path"):
        deploy_binds(
            [yaml.safe_dump({"services": {"x": {"volumes": ["./deploy/${NAME}.conf:/x"]}}})]
        )


# ── AC-2.2: the guard fails on every corruption, in both directions ──────────


def _with_new_bind(texts: list[str], source: str) -> list[str]:
    corrupted = copy.deepcopy(texts)
    loaded = copy.deepcopy(_load(corrupted[0]))
    loaded["services"]["nginx"]["volumes"].append(f"{source}:/etc/new:ro")
    corrupted[0] = yaml.safe_dump(loaded)
    return corrupted


def test_a_new_bind_nobody_listed_fails_naming_the_path() -> None:
    binds = deploy_binds(_with_new_bind(compose_texts(), "./deploy/new.conf"))
    _, listed = _real()
    problems = check(binds, listed, EXCLUDED, REQUIRED_EXTRA)
    assert problems == [
        "deploy/new.conf: needed by the Compose files but missing from bundle-files.txt"
    ]


def test_a_path_deleted_from_the_list_fails_naming_the_path() -> None:
    binds, listed = _real()
    for victim in ("deploy/nginx/nginx.conf", "deploy/vault/start.sh", "deploy/auto_size.py"):
        problems = check(binds, [p for p in listed if p != victim], EXCLUDED, REQUIRED_EXTRA)
        assert [victim in p for p in problems] == [True], problems


@pytest.mark.parametrize("forbidden", sorted(EXCLUDED))
def test_listing_an_excluded_path_fails_naming_the_path(forbidden: str) -> None:
    binds, listed = _real()
    problems = check(binds, [*listed, forbidden], EXCLUDED, REQUIRED_EXTRA)
    assert any(p.startswith(forbidden) and "excluded on purpose" in p for p in problems), problems


def test_an_exclusion_that_is_not_a_bind_fails_naming_the_path() -> None:
    binds, listed = _real()
    problems = check(binds, listed, {**EXCLUDED, "deploy/not-bound": "x"}, REQUIRED_EXTRA)
    assert problems == ["deploy/not-bound: excluded as a bind mount, but no Compose file binds it"]


def test_an_extra_listed_path_nothing_needs_fails_naming_the_path() -> None:
    binds, listed = _real()
    problems = check(binds, [*listed, "deploy/stray.sh"], EXCLUDED, REQUIRED_EXTRA)
    assert problems == ["deploy/stray.sh: listed in bundle-files.txt but nothing needs it"]


def test_the_list_has_34_distinct_paths() -> None:
    _, listed = _real()
    assert len(listed) == len(set(listed)) == 34, "9 fixed paths + the 25 bound ones (design 3.2.3)"
    assert "deploy/release/install.sh" not in listed, "bundle.sh adds install.sh by itself"


# ── AC-2.3 ───────────────────────────────────────────────────────────────────


def test_every_listed_path_is_tracked_by_git() -> None:
    _, listed = _real()
    for path in listed:
        tracked = subprocess.run(
            ["git", "ls-files", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert tracked, f"{path} is in bundle-files.txt but git does not track it"
        assert (REPO_ROOT / path).exists(), f"{path} is tracked but absent from the work tree"


def test_the_bundle_never_names_a_secret_or_a_dev_file() -> None:
    _, listed = _real()
    forbidden = ("deploy/load", "deploy/runpod", "certs", "init.json", "auto.conf", "tests", "src")
    for path in listed:
        parts = path.split("/")
        assert not any(bad in parts or path == bad for bad in forbidden), path
        assert not (parts[-1].startswith(".env") and parts[-1] != ".env.example"), path
    assert not any(
        p.endswith(("docker-compose.build.yml", "docker-compose.test.yml")) for p in listed
    )


# ── the cross-check against what Docker itself resolves ──────────────────────


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is not installed (NFR-11)")
def test_the_yaml_reading_agrees_with_docker_compose_config(tmp_path: Path) -> None:
    """A bind spelled in a way the YAML reader here does not know would be invisible to the
    guard. Compose resolves them all, so its answer is the cross-check. Runs in a copy, with a
    stand-in `.env` built from the template: never the real one."""
    for name in COMPOSE_FILES:
        shutil.copy(REPO_ROOT / name, tmp_path / name)
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env")
    empty_config = tmp_path / "docker-config"
    empty_config.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "DOCKER_CONFIG": str(empty_config),
    }
    done = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "*",
            "-f",
            COMPOSE_FILES[0],
            "-f",
            COMPOSE_FILES[1],
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"`docker compose config` is not usable here (exit {done.returncode})")
    resolved = json.loads(done.stdout)
    root = str(tmp_path) + "/"
    from_docker = {
        volume["source"].removeprefix(root).rstrip("/")
        for service in resolved["services"].values()
        for volume in service.get("volumes") or []
        if volume.get("type") == "bind" and volume["source"].startswith(root + "deploy/")
    }
    assert from_docker == deploy_binds(compose_texts())


# ── AC-6.1 (static half): nothing that holds the key is created before it exists ─────────────


def test_the_nine_carriers_are_exactly_the_services_that_name_the_secret() -> None:
    assert carriers(_services(compose_texts())) == CARRIERS


def test_vault_and_vault_bootstrap_do_not_pull_in_a_carrier() -> None:
    services = _services(compose_texts())
    reached = closure(services, ["vault", "vault-bootstrap"])
    assert reached == {"vault", "vault-bootstrap"}, sorted(reached)
    assert not reached & CARRIERS


def test_a_dependency_from_vault_bootstrap_to_a_carrier_would_be_caught() -> None:
    corrupted = copy.deepcopy(_services(compose_texts()))
    corrupted["vault-bootstrap"]["depends_on"] = {
        "vault": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
    }
    assert closure(corrupted, ["vault", "vault-bootstrap"]) & CARRIERS == {"migrate"}
    corrupted["vault"]["depends_on"] = ["app"]
    assert {"app", "migrate"} <= closure(corrupted, ["vault"]) & CARRIERS
