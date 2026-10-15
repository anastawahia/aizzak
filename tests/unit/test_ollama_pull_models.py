"""``deploy/ollama/pull_models.py`` -- the ``ollama-pull`` one-shot (server-gpu-plan.md 2.2).

gpu-compose design.md §3.4 and AC-2.3, NFR-10. The file is loaded by path (it is
not an importable package: it runs on a stock ``python:3.12-slim`` image) and its
``main`` is driven against a fake Ollama, a ``http.server`` on ``127.0.0.1:0``
that records every request and answers ``/api/version``, ``/api/show`` and
``/api/pull`` as scripted. No Docker, no network, no registry.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "deploy" / "ollama" / "pull_models.py"
_COMPOSE = _ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _ROOT / ".env.example"
_ROUTING_PREFIX = "${PROVIDER_ROUTING:-"

_SUCCESS_STREAM: list[dict[str, Any]] = [
    {"status": "pulling manifest"},
    {"status": "pulling 3c5d0000", "total": 10, "completed": 3},
    {"status": "pulling 3c5d0000", "total": 10, "completed": 10},
    {"status": "verifying sha256 digest"},
    {"status": "writing manifest"},
    {"status": "success"},
]


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ollama_pull_models", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm = _load_module()


def _table(**routes: dict[str, tuple[str, str]]) -> str:
    """``llm={"default": ("ollama", "gemma3:1b")}`` -> a PROVIDER_ROUTING JSON string."""
    return json.dumps(
        {
            namespace: {
                route: {"provider": provider, "model": model}
                for route, (provider, model) in entries.items()
            }
            for namespace, entries in routes.items()
        }
    )


def _compose_default_routing() -> str:
    raw = yaml.safe_load(_COMPOSE.read_text("utf-8"))["x-app-env"]["PROVIDER_ROUTING"]
    assert raw.startswith(_ROUTING_PREFIX) and raw.endswith("}")
    return str(raw.removeprefix(_ROUTING_PREFIX).removesuffix("}"))


def _env_example_value(name: str) -> str:
    for line in _ENV_EXAMPLE.read_text("utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{name} is not set in .env.example")


# ── a fake Ollama ───────────────────────────────────────────────────────────


class FakeOllama:
    def __init__(
        self,
        present: tuple[str, ...] = (),
        streams: dict[str, list[dict[str, Any]]] | None = None,
        *,
        show_status: int | None = None,
        pull_status: int | None = None,
        pull_adds_model: bool = True,
    ) -> None:
        self.present = set(present)
        self.streams = streams or {}
        self.requests: list[tuple[str, str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                outer.requests.append(("GET", self.path, None))
                if self.path == "/api/version":
                    self._json(200, {"version": "0.34.4"})
                else:
                    self._json(404, {"error": "no such path"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                outer.requests.append(("POST", self.path, body))
                model = body.get("model", "")
                if self.path == "/api/show":
                    if show_status is not None:
                        self._json(show_status, {"error": "scripted"})
                    elif model in outer.present:
                        self._json(200, {"details": {"family": "gemma3"}})
                    else:
                        self._json(404, {"error": f"model '{model}' not found"})
                elif self.path == "/api/pull":
                    if pull_status is not None:
                        self._json(pull_status, {"error": "scripted pull failure"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    for event in outer.streams.get(model, _SUCCESS_STREAM):
                        self.wfile.write(json.dumps(event).encode() + b"\n")
                        self.wfile.flush()
                    if pull_adds_model and not any(
                        "error" in e for e in outer.streams.get(model, [])
                    ):
                        outer.present.add(model)
                else:
                    self._json(404, {"error": "no such path"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def paths(self) -> list[str]:
        return [f"{method} {path}" for method, path, _ in self.requests]

    def pulled(self) -> list[str]:
        return [body["model"] for _, path, body in self.requests if path == "/api/pull"]


@pytest.fixture
def fake() -> Iterator[Callable[..., FakeOllama]]:
    made: list[FakeOllama] = []

    def make(*args: Any, **kwargs: Any) -> FakeOllama:
        instance = FakeOllama(*args, **kwargs)
        made.append(instance)
        return instance

    yield make
    for instance in made:
        instance.close()


def _env(routing: str, url: str, **extra: str) -> dict[str, str]:
    return {"PROVIDER_ROUTING": routing, "OLLAMA_BASE_URL": url, **extra}


# ── AC-2.3: which models ────────────────────────────────────────────────────


def test_the_compose_default_routing_pulls_exactly_gemma3_1b() -> None:
    assert pm.ollama_models(_compose_default_routing()) == ["gemma3:1b"]


def test_the_env_example_routing_pulls_exactly_gemma3_1b_fallback_route_included() -> None:
    routing = _env_example_value("PROVIDER_ROUTING")
    assert pm.ollama_models(routing) == ["gemma3:1b"]
    # LLM_FALLBACK_ROUTE is never read: the app refuses to boot unless it names a LOCAL route
    # of this same table, so its model is already in the list by the same rule.
    fallback = _env_example_value("LLM_FALLBACK_ROUTE")
    assert fallback, "the template sets a fallback route, so this test covers it"
    route = json.loads(routing)["llm"][fallback]
    assert route["provider"] == "ollama"
    assert route["model"] in pm.ollama_models(routing)


def test_a_mixed_table_pulls_each_ollama_model_once_and_nothing_else(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    routing = _table(
        llm={
            "default": ("ollama", "gemma3:1b"),
            "local": ("ollama", "gemma3:1b"),
            "summarize": ("ollama", "qwen3:4b"),
            "cloud": ("openai", "gpt-4.1-mini"),
        },
        embedding={"default": ("embedding-local", "sentence-transformers/x")},
        image={"default": ("image:openai", "gpt-image-1")},
    )
    assert pm.ollama_models(routing) == ["gemma3:1b", "qwen3:4b"]
    server = fake()
    assert pm.main(_env(routing, server.url)) == 0
    assert server.pulled() == ["gemma3:1b", "qwen3:4b"]
    assert "gpt-4.1-mini" not in capsys.readouterr().out


def test_the_provider_must_be_exactly_ollama() -> None:
    routing = _table(llm={"a": ("Ollama", "m1"), "b": ("ollama2", "m2"), "c": ("ollama", "m3")})
    assert pm.ollama_models(routing) == ["m3"]


def test_no_ollama_route_pulls_nothing_and_exits_0(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    server = fake()
    for routing in (_table(llm={"default": ("openai", "gpt-4.1-mini")}), "{}"):
        assert pm.main(_env(routing, server.url)) == 0
    assert server.requests == [], "no HTTP call at all when nothing routes to ollama"
    assert "no route uses ollama -- nothing to pull" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["{not json", "", "   ", "[]", '"x"', "null", "42"])
def test_invalid_json_exits_2_with_no_http_call(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    server = fake()
    assert pm.main(_env(bad, server.url)) == 2
    assert server.requests == []
    assert "PROVIDER_ROUTING" in capsys.readouterr().err


def test_the_routing_value_is_never_echoed(capsys: pytest.CaptureFixture[str]) -> None:
    canary = f"canary-{uuid.uuid4()}"
    unparsable = '{"llm": {"x": ' + canary
    malformed = json.dumps({"llm": {"x": {"provider": [canary], "model": "m"}}})
    assert pm.main({"PROVIDER_ROUTING": unparsable}) == 2
    assert pm.main({"PROVIDER_ROUTING": malformed}) == 2
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err


@pytest.mark.parametrize(
    ("routing", "path"),
    [
        ('{"llm": {"summarize": "oops"}}', "llm.summarize"),
        ('{"llm": []}', "llm"),
        ('{"llm": {"x": {"provider": "ollama"}}}', "llm.x.model"),
        ('{"llm": {"x": {"model": "m"}}}', "llm.x.provider"),
        ('{"llm": {"x": {"provider": "", "model": "m"}}}', "llm.x.provider"),
        ('{"llm": {"x": {"provider": 1, "model": "m"}}}', "llm.x.provider"),
        ('{"llm": {"x": {"provider": "ollama", "model": ""}}}', "llm.x.model"),
        ('{"llm": {"x": {"provider": "ollama", "model": ["m"]}}}', "llm.x.model"),
        ('{"embedding": {"default": null}}', "embedding.default"),
    ],
)
def test_a_malformed_entry_exits_2_naming_its_path(
    fake: Callable[..., FakeOllama],
    capsys: pytest.CaptureFixture[str],
    routing: str,
    path: str,
) -> None:
    server = fake()
    assert pm.main(_env(routing, server.url)) == 2
    assert server.requests == []
    assert path in capsys.readouterr().err


def test_a_bad_connect_wait_exits_2(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    server = fake()
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    # B-3: "inf" (and any spelling of it) used to be accepted and waited forever.
    for bad in ("abc", "-1", "nan", "inf", "-inf", "+inf", "Infinity", "1e999"):
        env = _env(routing, server.url, OLLAMA_PULL_CONNECT_WAIT_S=bad)
        assert pm.main(env) == 2, bad
    assert server.requests == []
    assert "OLLAMA_PULL_CONNECT_WAIT_S" in capsys.readouterr().err


def test_a_finite_connect_wait_is_still_accepted(fake: Callable[..., FakeOllama]) -> None:
    server = fake()
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    for good in ("0", "0.5", "30", " 5 "):
        env = _env(routing, server.url, OLLAMA_PULL_CONNECT_WAIT_S=good)
        assert pm.main(env) == 0, good


@pytest.mark.parametrize(
    "bad",
    [
        "ollama:11434",
        "localhost:11434",
        "ollama",
        "//ollama:11434",
        "ftp://ollama:11434",
        "file:///etc/passwd",
        "http://",
        "http://ollama:notaport",
        "http://ollama:99999",
    ],
)
def test_a_base_url_urllib_cannot_use_exits_2_at_once(
    bad: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # B-2: `ollama:11434` is a configuration error. It used to wait the whole
    # connect window and then exit 3 ("unreachable ... unknown url type").
    def never_wait(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a URL that cannot work must not be waited for")

    monkeypatch.setattr(pm, "wait_reachable", never_wait)
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    env = _env(routing, bad, OLLAMA_PULL_CONNECT_WAIT_S="30")
    assert pm.main(env) == 2
    assert "OLLAMA_BASE_URL" in capsys.readouterr().err


def test_a_rejected_base_url_is_never_echoed(capsys: pytest.CaptureFixture[str]) -> None:
    marker = f"marker-{uuid.uuid4().hex}"
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    assert pm.main(_env(routing, f"{marker}:11434")) == 2
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err


def test_an_https_base_url_passes_validation_and_an_unreachable_one_is_exit_3() -> None:
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    env = _env(routing, "https://127.0.0.1:9/", OLLAMA_PULL_CONNECT_WAIT_S="0")
    assert pm.main(env) == 3


def test_run_as_a_script_a_scheme_less_url_and_an_infinite_wait_exit_2_fast() -> None:
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    for extra in (
        {"OLLAMA_BASE_URL": "ollama:11434"},
        {"OLLAMA_BASE_URL": "http://127.0.0.1:9", "OLLAMA_PULL_CONNECT_WAIT_S": "inf"},
    ):
        done = subprocess.run(
            [sys.executable, "-I", str(_SCRIPT)],
            env={"PROVIDER_ROUTING": routing, **extra},
            capture_output=True,
            text=True,
            timeout=10,  # the bug waited 30 s, or forever
            check=False,
        )
        assert done.returncode == 2, (extra, done.stderr)
        assert "ollama-pull: ERROR: OLLAMA_" in done.stderr


# ── NFR-10: never pull what is already there ────────────────────────────────


def test_a_present_model_is_never_pulled(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    server = fake(present=("gemma3:1b",))
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    assert pm.main(_env(routing, server.url)) == 0
    assert server.pulled() == [], "zero /api/pull: a second run works offline"
    assert server.paths() == ["GET /api/version", "POST /api/show"]
    out = capsys.readouterr().out
    assert "ollama-pull: gemma3:1b already present -- not contacting the registry" in out


def test_an_absent_model_is_pulled_once_then_verified(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    server = fake()
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    assert pm.main(_env(routing, server.url)) == 0
    assert server.paths() == [
        "GET /api/version",
        "POST /api/show",
        "POST /api/pull",
        "POST /api/show",
    ]
    pull_body = next(body for _, path, body in server.requests if path == "/api/pull")
    assert pull_body == {"model": "gemma3:1b", "stream": True}
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("ollama-pull:")]
    statuses = [ln for ln in lines if "gemma3:1b: " in ln]
    assert len(statuses) == len(set(statuses)), "one line per change of status"
    assert any(ln.endswith("pulling manifest") for ln in statuses)
    assert any(ln.endswith("success") for ln in statuses)
    assert sum("pulling 3c5d0000" in ln for ln in statuses) == 1


def test_a_second_run_after_a_pull_contacts_no_registry(fake: Callable[..., FakeOllama]) -> None:
    server = fake()
    routing = _table(llm={"default": ("ollama", "gemma3:1b")})
    assert pm.main(_env(routing, server.url)) == 0
    assert pm.main(_env(routing, server.url)) == 0
    assert server.pulled() == ["gemma3:1b"]


# ── failures ────────────────────────────────────────────────────────────────


def test_a_pull_error_exits_4_naming_the_model(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    streams = {"gemma3:1b": [{"status": "pulling manifest"}, {"error": "manifest does not exist"}]}
    server = fake(streams=streams)
    assert pm.main(_env(_table(llm={"d": ("ollama", "gemma3:1b")}), server.url)) == 4
    err = capsys.readouterr().err
    assert "gemma3:1b" in err
    assert "manifest does not exist" in err


def test_one_failed_model_does_not_stop_the_others(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    server = fake(streams={"aaa:1b": [{"error": "pull model manifest: file does not exist"}]})
    routing = _table(llm={"a": ("ollama", "aaa:1b"), "b": ("ollama", "gemma3:1b")})
    assert pm.main(_env(routing, server.url)) == 4
    assert server.pulled() == ["aaa:1b", "gemma3:1b"]
    assert "gemma3:1b" in server.present
    assert "aaa:1b" in capsys.readouterr().err


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"pull_status": 500}, id="http-500"),
        pytest.param({"streams": {"gemma3:1b": [{"status": "pulling manifest"}]}}, id="no-success"),
        pytest.param({"pull_adds_model": False}, id="success-but-absent"),
    ],
)
def test_other_pull_failures_are_exit_4(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str], kwargs: dict[str, Any]
) -> None:
    server = fake(**kwargs)
    assert pm.main(_env(_table(llm={"d": ("ollama", "gemma3:1b")}), server.url)) == 4
    assert "gemma3:1b" in capsys.readouterr().err


def test_an_unreachable_ollama_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = _env(
        _table(llm={"d": ("ollama", "gemma3:1b")}),
        f"http://127.0.0.1:{port}",
        OLLAMA_PULL_CONNECT_WAIT_S="1",
    )
    assert pm.main(env) == 3
    assert "unreachable" in capsys.readouterr().err


def test_an_ollama_that_errors_on_show_is_exit_3(
    fake: Callable[..., FakeOllama], capsys: pytest.CaptureFixture[str]
) -> None:
    server = fake(show_status=500)
    assert pm.main(_env(_table(llm={"d": ("ollama", "gemma3:1b")}), server.url)) == 3
    assert server.pulled() == []
    assert "HTTP 500" in capsys.readouterr().err


def test_a_proxy_in_the_environment_is_never_used(
    fake: Callable[..., FakeOllama], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    server = fake(present=("gemma3:1b",))
    assert pm.main(_env(_table(llm={"d": ("ollama", "gemma3:1b")}), server.url)) == 0


def test_the_base_url_defaults_to_the_in_stack_service() -> None:
    assert pm.DEFAULT_BASE_URL == "http://ollama:11434"


# ── the file itself ─────────────────────────────────────────────────────────


def test_the_file_imports_the_standard_library_only() -> None:
    tree = ast.parse(_SCRIPT.read_text("utf-8"))
    roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    assert roots, "the import scan matched nothing"
    assert roots <= set(sys.stdlib_module_names), sorted(roots - set(sys.stdlib_module_names))


def test_run_as_a_script_it_exits_2_on_bad_routing_and_echoes_no_environment() -> None:
    canary = f"canary-{uuid.uuid4()}"
    done = subprocess.run(
        [sys.executable, "-I", str(_SCRIPT)],
        env={"PROVIDER_ROUTING": "{not json", "CANARY_SECRET": canary},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 2
    assert "ollama-pull: ERROR: PROVIDER_ROUTING is not valid JSON" in done.stderr
    assert canary not in done.stdout + done.stderr
