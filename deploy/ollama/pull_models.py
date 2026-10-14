"""ollama-pull (server-gpu-plan.md 2.2): pull every Ollama model PROVIDER_ROUTING
routes to into the in-stack `ollama` service -- and nothing already there, so a
re-run contacts no registry and works offline. Standard library only: it runs on
a stock python:3.12-slim image (docker-compose.yml, service `ollama-pull`).

Environment: PROVIDER_ROUTING (required, the table `app` resolves),
OLLAMA_BASE_URL (default http://ollama:11434), OLLAMA_PULL_CONNECT_WAIT_S
(default 30, how long to wait for Ollama to answer).

Exit codes: 0 done (including "no route uses ollama") · 2 the configuration is not
usable (PROVIDER_ROUTING) · 3 Ollama unreachable · 4 a pull failed (the model is
named; the other models are still attempted).

Nothing here echoes the environment: the log names models, never values.
"""

from __future__ import annotations

import http
import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

OLLAMA_PROVIDER = "ollama"
DEFAULT_BASE_URL = "http://ollama:11434"
DEFAULT_CONNECT_WAIT_S = 30.0
EXIT_OK = 0
EXIT_ROUTING = 2
EXIT_UNREACHABLE = 3
EXIT_PULL_FAILED = 4
_TRANSPORT_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError)


class RoutingError(ValueError):
    """The configuration cannot be used (exit 2). The message never holds a value."""


class OllamaError(RuntimeError):
    """Ollama did not do what was asked. `unreachable` picks exit 3 over exit 4."""

    def __init__(self, message: str, *, unreachable: bool = False) -> None:
        super().__init__(message)
        self.unreachable = unreachable


def _log(message: str) -> None:
    sys.stdout.write(f"ollama-pull: {message}\n")
    sys.stdout.flush()


def _fail(message: str) -> None:
    sys.stderr.write(f"ollama-pull: ERROR: {message}\n")
    sys.stderr.flush()


def ollama_models(routing_json: str) -> list[str]:
    """Every distinct `model` whose `provider` is exactly "ollama", across every
    namespace and route, sorted. Invalid JSON, a non-object namespace or entry,
    or a non-string/empty provider or model raises RoutingError naming the
    offending path (e.g. `llm.summarize`). The value itself is never echoed."""
    if not routing_json.strip():
        raise RoutingError("PROVIDER_ROUTING is empty or not set")
    try:
        table = json.loads(routing_json)
    except json.JSONDecodeError as err:
        raise RoutingError(
            f"PROVIDER_ROUTING is not valid JSON: {err.msg} (line {err.lineno}, column {err.colno})"
        ) from None
    if not isinstance(table, dict):
        raise RoutingError("PROVIDER_ROUTING must be a JSON object of namespaces")
    models: set[str] = set()
    for namespace, routes in table.items():
        if not isinstance(routes, dict):
            raise RoutingError(f"PROVIDER_ROUTING.{namespace} must be an object of routes")
        for route, entry in routes.items():
            path = f"{namespace}.{route}"
            if not isinstance(entry, dict):
                raise RoutingError(f"PROVIDER_ROUTING.{path} must be an object")
            provider = entry.get("provider")
            model = entry.get("model")
            if not isinstance(provider, str) or not provider.strip():
                raise RoutingError(f"PROVIDER_ROUTING.{path}.provider must be a non-empty string")
            if not isinstance(model, str) or not model.strip():
                raise RoutingError(f"PROVIDER_ROUTING.{path}.model must be a non-empty string")
            if provider == OLLAMA_PROVIDER:
                models.add(model)
    return sorted(models)


def _opener() -> urllib.request.OpenerDirector:
    # No proxy, whatever the environment says: Ollama is a service on this
    # network, and a proxy variable must never route a model pull elsewhere.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, body: dict[str, Any], timeout_s: float) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _opener().open(request, timeout=timeout_s)


def _error_text(err: urllib.error.HTTPError) -> str:
    try:
        raw = err.read(2000).decode("utf-8", errors="replace")
    except OSError:
        return ""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw.strip()
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        return str(parsed["error"])
    return raw.strip()


def is_present(base_url: str, model: str, *, timeout_s: float = 10.0) -> bool:
    """POST {base}/api/show {"model": model}: 200 -> True, 404 -> False,
    anything else -> OllamaError. Reads the local manifest only."""
    try:
        with _post(f"{base_url}/api/show", {"model": model}, timeout_s) as response:
            if response.status == http.HTTPStatus.OK:
                return True
            raise OllamaError(
                f"/api/show for {model} answered HTTP {response.status}", unreachable=True
            )
    except urllib.error.HTTPError as err:
        if err.code == http.HTTPStatus.NOT_FOUND:
            return False
        raise OllamaError(
            f"/api/show for {model} answered HTTP {err.code}", unreachable=True
        ) from err
    except _TRANSPORT_ERRORS as err:
        raise OllamaError(f"Ollama unreachable at {base_url}: {err}", unreachable=True) from err


def _consume_pull_stream(model: str, response: Any) -> None:
    last_status: str | None = None
    succeeded = False
    for raw in response:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise OllamaError(f"pull of {model}: unreadable progress line") from None
        if not isinstance(event, dict):
            raise OllamaError(f"pull of {model}: unexpected progress line")
        if "error" in event:
            raise OllamaError(f"pull of {model} failed: {event['error']}")
        status = event.get("status")
        if isinstance(status, str) and status != last_status:
            _log(f"{model}: {status}")
            last_status = status
            succeeded = succeeded or status == "success"
    if not succeeded:
        raise OllamaError(f"pull of {model} ended without success")


def pull(base_url: str, model: str, *, read_timeout_s: float = 600.0) -> None:
    """POST {base}/api/pull {"model": model, "stream": true}. One output line
    per change of `status`; an `error` line, an HTTP error, or a stream that
    ends without `"status": "success"` raises OllamaError."""
    try:
        with _post(
            f"{base_url}/api/pull", {"model": model, "stream": True}, read_timeout_s
        ) as response:
            _consume_pull_stream(model, response)
    except urllib.error.HTTPError as err:
        raise OllamaError(f"pull of {model} failed: HTTP {err.code} {_error_text(err)}") from err
    except _TRANSPORT_ERRORS as err:
        raise OllamaError(f"pull of {model} failed: {err}") from err


def wait_reachable(base_url: str, *, within_s: float) -> None:
    """GET {base}/api/version until 200, once a second; OllamaError after
    `within_s` (OLLAMA_PULL_CONNECT_WAIT_S, default 30)."""
    deadline = time.monotonic() + within_s
    last = "no answer"
    while True:
        try:
            with _opener().open(f"{base_url}/api/version", timeout=5) as response:
                if response.status == http.HTTPStatus.OK:
                    return
                last = f"HTTP {response.status}"
        except _TRANSPORT_ERRORS as err:
            last = str(err)
        if time.monotonic() >= deadline:
            raise OllamaError(
                f"Ollama unreachable at {base_url} after {within_s:g}s: {last}", unreachable=True
            )
        time.sleep(1.0)


def _connect_wait_s(environ: Mapping[str, str]) -> float:
    raw = environ.get("OLLAMA_PULL_CONNECT_WAIT_S", "").strip()
    if not raw:
        return DEFAULT_CONNECT_WAIT_S
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value < 0 or math.isnan(value):
        raise RoutingError("OLLAMA_PULL_CONNECT_WAIT_S must be a non-negative number of seconds")
    return value


def _ensure_model(base_url: str, model: str) -> int:
    """Make one model present. Returns an exit code (0 = it is there)."""
    try:
        if is_present(base_url, model):
            _log(f"{model} already present -- not contacting the registry")
            return EXIT_OK
        _log(f"{model} not present -- pulling")
        pull(base_url, model)
        if not is_present(base_url, model):
            raise OllamaError(f"pull of {model} finished but the model is still not present")
    except OllamaError as err:
        _fail(str(err))
        return EXIT_UNREACHABLE if err.unreachable else EXIT_PULL_FAILED
    _log(f"{model} pulled")
    return EXIT_OK


def main(environ: Mapping[str, str] = os.environ) -> int:
    try:
        models = ollama_models(environ.get("PROVIDER_ROUTING", ""))
        wait_s = _connect_wait_s(environ)
    except RoutingError as err:
        _fail(str(err))
        return EXIT_ROUTING
    if not models:
        _log("no route uses ollama -- nothing to pull")
        return EXIT_OK
    base_url = environ.get("OLLAMA_BASE_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL
    try:
        wait_reachable(base_url, within_s=wait_s)
    except OllamaError as err:
        _fail(str(err))
        return EXIT_UNREACHABLE
    worst = EXIT_OK
    for model in models:
        code = _ensure_model(base_url, model)
        if code == EXIT_UNREACHABLE:
            return code
        worst = max(worst, code)
    if worst == EXIT_OK:
        _log(f"done: {len(models)} model(s) present")
    return worst


if __name__ == "__main__":
    sys.exit(main())
