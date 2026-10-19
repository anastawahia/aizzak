"""The fake `docker`, `curl` and `df` for the install tests (see support_install.py).

This file is COPIED three times into a temporary `bin/` (as `docker`, `curl` and `df`) and run
as a script, so it imports the standard library only and finds its data relative to itself:

    <fake>/bin/<tool>   this file          <fake>/model.json   the Compose files as data
    <fake>/state.json   images, containers <fake>/calls.jsonl  one line per call

Every call is logged with argv, cwd, the whole environment and the sha256 of stdin (stdin itself is
never written down). Behaviour is steered by FAKE_* variables the test puts in the environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

BIN = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.dirname(BIN)
STATE = os.path.join(FAKE, "state.json")
CALLS = os.path.join(FAKE, "calls.jsonl")
TOOL = os.path.basename(sys.argv[0])
ARGS = sys.argv[1:]
DEFAULT_SECRET_ID = "5b2a1c9e-4d3f-4a6b-8c7d-0e1f2a3b4c5d"


def load_state() -> dict:
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as handle:
            return json.load(handle)
    faults = json.loads(os.environ.get("FAKE_FAULTS", "{}"))
    return {"images": [], "containers": [], "faults": faults, "n": 0}


def save_state(state: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def log(stdin_sha256: str = "") -> None:
    record = {
        "tool": TOOL,
        "argv": ARGS,
        "cwd": os.getcwd(),
        "env": dict(os.environ),
        "stdin_sha256": stdin_sha256,
    }
    with open(CALLS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def finish(text: str = "", rc: int = 0, *, err: bool = False) -> None:
    if text:
        stream = sys.stderr if err else sys.stdout
        stream.write(text if text.endswith("\n") else text + "\n")
    sys.exit(rc)


def curl() -> None:
    log()
    codes = json.loads(os.environ.get("FAKE_CURL", "{}"))
    key = "ready" if ARGS[-1].endswith("/health/ready") else "health"
    sys.stdout.write(str(codes.get(key, 200)))
    sys.exit(0)


def df() -> None:
    log()
    kb = os.environ.get("FAKE_DF_KB", str(400 * 1024 * 1024))
    sys.stdout.write(
        f"Filesystem 1024-blocks Used Available Capacity Mounted on\nfake 1 1 {kb} 1% /\n"
    )
    sys.exit(0)


def read_env(project: str | None) -> tuple[str, dict[str, str]]:
    values: dict[str, str] = {}
    text = ""
    path = os.path.join(project or os.getcwd(), ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for line in text.splitlines():
            found = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
            if found:
                values[found.group(1)] = found.group(2).strip().strip("'\"")
    return text, values


def active_services(values: dict[str, str]) -> tuple[dict, list[str]]:
    with open(os.path.join(FAKE, "model.json"), encoding="utf-8") as handle:
        model = json.load(handle)
    gpu = "docker-compose.gpu.yml" in values.get("COMPOSE_FILE", "")
    services = model["gpu" if gpu else "cpu"]
    wanted = {p for p in values.get("COMPOSE_PROFILES", "").split(",") if p}
    names = [n for n, s in services.items() if not s["profiles"] or set(s["profiles"]) & wanted]
    return services, names


def interpolate(text: str, values: dict[str, str]) -> str:
    return re.sub(
        r"\$\{([A-Z0-9_]+):?-([^}]*)\}", lambda m: values.get(m.group(1)) or m.group(2), text
    )


def config_hash(service: str, values: dict[str, str], names: list[str]) -> str:
    """Compose hashes the service's own configuration: here, the service name and the value of
    every variable its definition mentions (so VAULT_SECRET_ID counts for the services that carry
    it and for no other, which is what design.md 1 measured)."""
    body = "\n".join(f"{name}={values.get(name, '')}" for name in names)
    return hashlib.sha256((service + "\n" + body).encode()).hexdigest()[:16]


def dependency_closure(services: dict, names: list[str], roots: list[str]) -> list[str]:
    seen: list[str] = []
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen or name not in names:
            continue
        seen.append(name)
        todo.extend(services[name]["depends_on"])
    return seen


def plain_docker(rest: list[str]) -> None:
    log()
    if rest[:1] == ["info"]:
        finish(os.environ.get("FAKE_DOCKER_ROOT", "/var/lib/docker"))
    if rest[:2] == ["manifest", "inspect"]:
        message = os.environ.get("FAKE_MANIFEST_ERR", "")
        if message:
            finish(message, int(os.environ.get("FAKE_MANIFEST_RC", "1")), err=True)
        finish("{}")
    if rest[:2] == ["image", "inspect"]:
        ref = rest[-1]
        if ref not in load_state()["images"]:
            finish("Error: No such image: " + ref, 1, err=True)
        if "--format" in rest:
            bad = os.environ.get("FAKE_BAD_DIGEST_FOR", "")
            finish("anastawahia/aizzak@sha256:" + "f" * 64 if bad and bad in ref else ref)
        finish("[{}]")
    if rest[:1] == ["pull"]:
        state = load_state()
        ref = rest[1]
        fail = os.environ.get("FAKE_PULL_FAIL", "")
        if fail and fail in ref:
            finish("Error response from daemon: pull access denied for " + ref, 1, err=True)
        if ref not in state["images"]:
            state["images"].append(ref)
        save_state(state)
        finish("pulled " + ref)
    if rest[:1] == ["inspect"]:
        state = load_state()
        ids = rest[rest.index("--format") + 2 :] if "--format" in rest else rest[1:]
        rows = [
            "|".join(
                [c["service"], c["hash"], c["status"], c["health"], str(c["exit"]), c["restart"]]
            )
            for c in state["containers"]
            if c["id"] in ids
        ]
        finish("\n".join(rows))
    finish(f"fake docker: unexpected call {ARGS!r}", 64, err=True)


def compose_up(
    state: dict, services: dict, names: list[str], values: dict[str, str], rest: list[str]
) -> None:
    roots = [a for a in rest[1:] if not a.startswith("-")] or names
    for name in dependency_closure(services, names, roots):
        digest = config_hash(name, values, services[name]["vars"])
        have = [c for c in state["containers"] if c["service"] == name]
        failed = any(c["status"] == "exited" and c["exit"] != 0 for c in have)
        if have and all(c["hash"] == digest for c in have) and not failed:
            continue  # a finished container is left alone; a failed one is run again
        state["containers"] = [c for c in state["containers"] if c["service"] != name]
        state["n"] += 1
        one_shot = services[name]["restart"] == "no"
        fault = state["faults"].get(name, {})
        status, health, code = ("exited", "", 0) if one_shot else ("running", "healthy", 0)
        if fault.get("exit") and fault.get("times", 0) > 0 and one_shot:
            code = int(fault["exit"])
            fault["times"] -= 1
        if fault.get("health") and not one_shot:
            health = fault["health"]
        state["containers"].append(
            {
                "id": f"c{state['n']:04d}",
                "service": name,
                "hash": digest,
                "status": status,
                "health": health,
                "exit": code,
                "restart": services[name]["restart"],
            }
        )
    save_state(state)
    finish("started")


def compose_exec(rest: list[str]) -> None:
    body = b"" if sys.stdin.isatty() else sys.stdin.buffer.read()
    log(hashlib.sha256(body).hexdigest())
    target = next(a for a in rest[1:] if a != "-T")
    if target == "vault":
        rc = int(os.environ.get("FAKE_MINT_RC", "0"))
        if rc:
            finish("Error writing data: hvs.PLANTEDTOKENFORTHELEAKCHECK0123456789", rc, err=True)
        finish(os.environ.get("FAKE_MINT_OUTPUT", DEFAULT_SECRET_ID))
    if target == "postgres":
        finish("", int(os.environ.get("FAKE_PG_RC", "0")))
    if target == "app":
        rc = int(os.environ.get("FAKE_SMOKE_RC", "0"))
        finish("stack_smoke: PASS" if rc == 0 else "stack_smoke: FAIL", rc)
    finish("")


def docker() -> None:
    rest = list(ARGS)
    if rest[:1] != ["compose"]:
        plain_docker(rest)
    rest = rest[1:]
    project = None
    if rest[:1] == ["--project-directory"]:
        project, rest = rest[1], rest[2:]
    _, values = read_env(project)
    services, names = active_services(values)
    sub = rest[0] if rest else ""
    if sub == "exec":
        compose_exec(rest)
    log()
    if sub == "config":
        if "--images" in rest:
            images = {
                interpolate(services[n]["image"], values) for n in names if services[n]["image"]
            }
            finish("\n".join(sorted(images)))
        if "--services" in rest:
            finish("\n".join(names))
        if "--hash" in rest:
            rows = (f"{n} {config_hash(n, values, services[n]['vars'])}" for n in names)
            finish("\n".join(rows))
        finish("fake compose: a bare config is refused here", 64, err=True)
    if sub == "up":
        compose_up(load_state(), services, names, values, rest)
    if sub == "ps":
        wanted = [a for a in rest[1:] if not a.startswith("-")]
        ids = [c["id"] for c in load_state()["containers"] if not wanted or c["service"] in wanted]
        finish("\n".join(ids))
    if sub == "port":
        finish("0.0.0.0:" + values.get("HOST_PORT_HTTP", "80"))
    if sub == "logs":
        finish("fake logs of " + " ".join(rest[1:]))
    finish(f"fake docker: unexpected compose call {ARGS!r}", 64, err=True)


if __name__ == "__main__":
    {"curl": curl, "df": df}.get(TOOL, docker)()
