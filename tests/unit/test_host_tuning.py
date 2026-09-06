"""Which kernel settings belong to the HOST and which belong to the container
that uses them -- capacity step 3.5 (docs/capacity-plan.md §5 wave 3).

⭐ THE STEP NAMES FIVE KNOBS AND FOUR OF THEM ARE NOT THE HOST'S TO SET. That
is the finding, and this module is what stops it being made again. `somaxconn`,
`ip_local_port_range` and `tcp_tw_reuse` are NETWORK-NAMESPACED -- a container
carries its own copy and the host's is never consulted -- and `nofile` is not a
sysctl at all, it is `RLIMIT_NOFILE`, handed down from the Docker daemon.

The kernel draws the line itself and it is readable. Inside a container's
network namespace `/proc/sys/net/core/` holds exactly seven entries:

    rps_default_mask  somaxconn  txrehash  xfrm_acq_expires
    xfrm_aevent_etime  xfrm_aevent_rseqth  xfrm_larval_drop

`netdev_max_backlog` is not among them -- what a container may own a private
copy of is there, what it may not does not exist there at all. Docker enforces
the same line from the other side, in so many words:

    $ docker run --sysctl net.core.somaxconn=1024 alpine:3 true      # accepted
    $ docker run --sysctl vm.overcommit_memory=1  alpine:3 true
    invalid argument "vm.overcommit_memory=1" for "--sysctl" flag:
    sysctl 'vm.overcommit_memory=1' is not allowed

⭐ AND THIS IS THE THIRD TIME THIS REPOSITORY HAS MET THE SHAPE. 3.1 found
`nofile` was not the host's (`fs.file-max` here is 9223372036854775807 while a
gunicorn worker sat pinned at 1024). 3.2 found the host's own
`ip_local_port_range` was 4,096 ports -- SIX TIMES NARROWER than the container
default it would have "fixed". 3.5 adds `somaxconn`. A host script that writes
a namespaced knob changes nothing while reading exactly like a fix, so the two
files must PARTITION the knobs, and `test_no_knob_is_claimed_by_both_files` is
that partition.

⚠️ `somaxconn` IS THE CLAMP, NOT THE BACKLOG, and the difference is silent.
`listen(backlog)` is reduced to `min(backlog, somaxconn)` with no error and no
log line. MEASURED by connecting until the kernel started dropping and reading
`TcpExtListenOverflows` on either side of the run:

    somaxconn=4096   listen( 511) queued  512   listen(2048) queued 2049
    somaxconn=128    listen( 511) queued  129   listen(2048) queued  129

nginx asks for 511 (its built-in default; no `listen ... backlog=` states one)
and gunicorn for 2048 (its default; nothing in this repository states it
either). The kernel's default for `somaxconn` has only been 4096 since Linux
5.4 -- before that it was 128, which is the second row -- so on an older
production kernel this platform's edge would queue 129 sockets against §0's
1,500 and its x6 login peak. That is why the value is now STATED in
`docker-compose.yml` rather than inherited, and why the last tests here compare
the promise with the resource, the same pairing 3.1 drew between
`worker_connections` and `worker_rlimit_nofile`.

⚠️ AND AN OVERFLOWING ACCEPT QUEUE DOES NOT REFUSE THE CLIENT. With
`tcp_abort_on_overflow=0`, the default, the kernel drops the final ACK and the
server retransmits its SYN-ACK on the 1s/2s/4s schedule -- so the symptom is a
latency tail on a fraction of connections, not an error anybody counts. The
only counter that names it is `TcpExtListenOverflows`, which is per-namespace
and scraped by nothing (`د-17`).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SCRIPT = _REPO_ROOT / "deploy" / "host-tuning.sh"
_EDGE = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_RUNPOD_EDGE = _REPO_ROOT / "deploy" / "runpod" / "nginx.conf"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_RUNPOD_SUPERVISOR = _REPO_ROOT / "deploy" / "runpod" / "supervisord.conf"

# nginx's own default when a `listen` directive carries no `backlog=`
# (`NGX_LISTEN_BACKLOG`, 511 on Linux), and gunicorn's when no `--backlog` is
# passed. Both are restated here for the reason `test_connection_budget.py`
# restates Postgres's `max_connections`: a ceiling nobody wrote down is still a
# ceiling, and the tests below have to compare against SOMETHING.
_NGINX_DEFAULT_BACKLOG = 511
_GUNICORN_DEFAULT_BACKLOG = 2048

# The listeners whose accept queue faces §0's population. `pgbouncer` is
# deliberately not here: its clients are long-lived pool connections opened at
# boot, not a login burst, and its own ceiling is 2.2's subject.
_LISTENING_SERVICES = ("nginx", "app")


def _services(text: str | None = None) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(
        text if text is not None else _COMPOSE.read_text(encoding="utf-8")
    )
    services: dict[str, Any] = loaded["services"]
    return services


def _sysctls(service_name: str) -> dict[str, str]:
    declared = _services()[service_name].get("sysctls") or {}
    if isinstance(declared, list):  # Compose also accepts `- key=value`
        return dict(entry.split("=", 1) for entry in declared)
    return {str(key): str(value) for key, value in declared.items()}


def _script_knobs(text: str | None = None) -> list[str]:
    """The sysctl names the script actually WRITES -- the rows of its `KNOBS`
    heredoc, not the ones it merely names in its closing report."""
    body = text if text is not None else _SCRIPT.read_text(encoding="utf-8")
    table = re.search(r"KNOBS=\$\(\n\s*cat <<'TABLE'\n(.*?)\nTABLE", body, re.DOTALL)
    assert table is not None, "the script's KNOBS table has been restructured"
    return [line.split("\t")[0] for line in table.group(1).splitlines() if line.strip()]


def _compose_sysctl_names(text: str | None = None) -> set[str]:
    names: set[str] = set()
    for service in _services(text).values():
        declared = service.get("sysctls") or {}
        names.update(
            entry.split("=", 1)[0] if isinstance(declared, list) else str(entry)
            for entry in declared
        )
    return names


def _nginx_backlog(text: str) -> int:
    """The accept queue the edge ASKS the kernel for, across every `listen`."""
    asked = [
        int(match.group("backlog"))
        for match in re.finditer(r"^\s*listen\s+[^;]*?backlog=(?P<backlog>\d+)", text, re.MULTILINE)
    ]
    listens = len(re.findall(r"^\s*listen\s+\d", text, re.MULTILINE))
    if listens > len(asked):
        asked.append(_NGINX_DEFAULT_BACKLOG)
    return max(asked)


def _gunicorn_backlog() -> int:
    match = re.search(r'"--backlog",\s*"(?P<value>\d+)"', _DOCKERFILE.read_text(encoding="utf-8"))
    return int(match.group("value")) if match else _GUNICORN_DEFAULT_BACKLOG


# ── The partition: what the host owns, and what it must not claim ────────────


def test_no_knob_is_claimed_by_both_files() -> None:
    """A sysctl written in BOTH `deploy/host-tuning.sh` and a Compose
    `sysctls:` block is the bug this whole step is about: the host copy is inert
    and the two would drift without anything noticing which one won."""
    overlap = set(_script_knobs()) & _compose_sysctl_names()
    assert not overlap, (
        f"{sorted(overlap)} is set on the host AND per container. A namespaced knob "
        "written on the host changes nothing -- pick the file where the process lives"
    )


def test_the_host_script_never_writes_a_namespaced_knob() -> None:
    """The rule stated as the kernel states it. `net.*` is namespaced with a
    handful of exceptions, and every knob 3.5 named from that tree was measured
    to be one a container carries its own copy of."""
    namespaced = [knob for knob in _script_knobs() if knob.startswith("net.core.somaxconn")]
    namespaced += [knob for knob in _script_knobs() if knob.startswith("net.ipv4.")]
    assert not namespaced, (
        f"{namespaced} is network-namespaced: `docker run --sysctl` accepts it, which is "
        "exactly why writing it on the host is inert. It belongs in docker-compose.yml"
    )


def test_the_knobs_moved_out_are_still_named_where_they_went() -> None:
    """Deleting a knob from the host script is only half the correction -- an
    operator following 3.5 has to be told where it actually went, or the next
    person re-adds it. The script's closing report is that half."""
    report = _SCRIPT.read_text(encoding="utf-8")
    for knob in ("net.core.somaxconn", "net.ipv4.ip_local_port_range", "net.ipv4.tcp_tw_reuse"):
        assert re.search(rf"^elsewhere {re.escape(knob)}\s+docker-compose\.yml", report, re.M), (
            f"{knob} left the host script without the report saying where it went"
        )
    nofile = re.search(r"^elsewhere nofile .*?(?=^elsewhere |^ELSEWHERE|\Z)", report, re.M | re.S)
    assert nofile is not None, "`nofile` left the host script with nothing said about it"
    assert "supervisord.conf" in nofile.group() and "not a sysctl" in nofile.group(), (
        "`nofile` is RLIMIT_NOFILE, not a sysctl, and the report must say where it IS set "
        "on both publishers (capacity 3.1)"
    )


# ── The acceptance criterion, run rather than asserted ───────────────────────


def _run(tmp_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "SYSCTL_ROOT": str(tmp_root)},
        check=False,
    )


def _fake_host(tmp_path: Path, **values: str) -> Path:
    root = tmp_path / "sys"
    for knob, value in values.items():
        path = root / Path(knob.replace(".", "/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{value}\n", encoding="utf-8")
    return root


def test_the_script_is_valid_bash() -> None:
    """No linter in this repository's five gates reads shell, so the syntax
    check is here or nowhere."""
    assert subprocess.run(["bash", "-n", str(_SCRIPT)], check=False).returncode == 0


def test_running_it_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    """3.5's acceptance criterion in full: "the script is a no-op on repeat, and
    prints what it changed". Both halves are EXECUTED here against a throwaway
    tree rather than asserted about the source, which is why the script takes a
    `SYSCTL_ROOT` at all."""
    root = _fake_host(tmp_path, **dict.fromkeys(_script_knobs(), "0"))

    first = _run(root)
    assert first.returncode == 0, first.stderr
    assert "1 changed" in first.stdout, first.stdout
    assert re.search(r"^changed vm\.overcommit_memory\s+0 -> 1", first.stdout, re.M), first.stdout

    second = _run(root)
    assert second.returncode == 0, second.stderr
    assert "0 changed" in second.stdout, second.stdout
    assert "changed " not in second.stdout.split("\n\n")[0], second.stdout


def test_check_mode_reports_the_drift_and_refuses_to_pass(tmp_path: Path) -> None:
    """`--check` is what a CI job or a pre-flight would run: it writes nothing
    and it fails while the host is not what this stack needs."""
    root = _fake_host(tmp_path, **dict.fromkeys(_script_knobs(), "0"))

    drifting = _run(root, "--check")
    assert drifting.returncode == 1, drifting.stdout
    assert "DRIFT" in drifting.stdout
    assert (root / "vm" / "overcommit_memory").read_text().strip() == "0", (
        "--check wrote to the host; it is a report, not an apply"
    )

    _run(root)
    assert _run(root, "--check").returncode == 0


def test_it_says_so_rather_than_lying_when_a_knob_is_not_there(tmp_path: Path) -> None:
    """A kernel without one of these files (or a `SYSCTL_ROOT` pointed at the
    wrong place) must be visible. Silently counting an absent knob as "ok" is
    how a tuning script comes to certify a machine it never touched."""
    result = _run(_fake_host(tmp_path))
    assert "absent" in result.stdout
    assert "1 absent" in result.stdout


# ── The promise and the resource behind it ───────────────────────────────────


def test_the_edge_never_asks_for_a_deeper_queue_than_the_kernel_will_give_it() -> None:
    """`listen(backlog)` is clamped to `somaxconn` silently -- measured,
    listen(2048) queued 129 under somaxconn=128. Same pairing as 3.1's
    `worker_connections` against `worker_rlimit_nofile`: the promise is worth
    only what the resource behind it says."""
    for service, asked in (
        ("nginx", _nginx_backlog(_EDGE.read_text(encoding="utf-8"))),
        ("app", _gunicorn_backlog()),
    ):
        declared = _sysctls(service).get("net.core.somaxconn")
        assert declared is not None, (
            f"`{service}` states no `net.core.somaxconn`, so its accept queue is whatever "
            "the kernel's default happens to be -- 4096 since Linux 5.4 and 128 before it"
        )
        assert asked <= int(declared), (
            f"`{service}` asks the kernel for a backlog of {asked} against a somaxconn of "
            f"{declared}; the kernel will clamp it to {declared} and say nothing"
        )


def test_every_listener_facing_the_login_burst_states_its_own_queue() -> None:
    """The 3.1 correction applied one layer down: a capacity that changes with
    whichever kernel the container lands on is not a capacity anyone declared."""
    for service in _LISTENING_SERVICES:
        assert "net.core.somaxconn" in _sysctls(service), (
            f"`{service}` accepts §0's connections and inherits its accept queue ceiling"
        )


def test_the_runpod_edge_cannot_raise_what_compose_states(tmp_path: Path) -> None:
    """The second publisher, and the reason its number is different rather than
    drifted: `deploy/runpod/` has no Compose file and supervisord cannot set a
    sysctl, so that edge lives on whatever `somaxconn` its container was given.
    Its `listen` may therefore not promise more than the kernel default this
    step measured -- there is nothing on that path that could back it."""
    del tmp_path
    asked = _nginx_backlog(_RUNPOD_EDGE.read_text(encoding="utf-8"))
    assert asked <= 4096, (
        f"the RunPod edge asks for a backlog of {asked} and nothing on that path can raise "
        "somaxconn to meet it; supervisord has no sysctl of any kind (grep `minfds` for the "
        "one limit it CAN set, capacity 3.1)"
    )
    assert "sysctl" not in _RUNPOD_SUPERVISOR.read_text(encoding="utf-8").lower(), (
        "supervisord.conf appears to set a sysctl, which supervisor cannot do"
    )


def test_these_guards_can_actually_fail() -> None:
    """Every guard above, shown failing on the shape it prevents -- the same
    demonstration `test_edge_capacity.py` and `test_gunicorn_flags.py` end on.
    A guard never seen to fail is a guard nobody has checked."""
    smuggled = _SCRIPT.read_text(encoding="utf-8").replace(
        "vm.overcommit_memory\t1\t",
        "net.core.somaxconn\t4096\tinert\nvm.overcommit_memory\t1\t",
    )
    assert "net.core.somaxconn" in _script_knobs(smuggled), "the KNOBS parser missed a row"
    assert set(_script_knobs(smuggled)) & _compose_sysctl_names(), (
        "a namespaced knob smuggled into the host script did not collide with the Compose "
        "declaration -- the partition test would have passed it"
    )

    unstated = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    for service in _LISTENING_SERVICES:
        del unstated["services"][service]["sysctls"]["net.core.somaxconn"]
    assert "net.core.somaxconn" not in _compose_sysctl_names(yaml.safe_dump(unstated)), (
        "an edge that states no accept queue read as though it did"
    )

    assert _nginx_backlog("    listen 443 ssl;\n") == _NGINX_DEFAULT_BACKLOG
    assert _nginx_backlog("    listen 443 ssl backlog=8192;\n") == 8192
    assert _nginx_backlog("    listen 80 backlog=8192;\n    listen 443 ssl;\n") == 8192
