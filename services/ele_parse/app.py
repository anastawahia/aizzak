"""The ele-parse HTTP server (standard library only, ``INC1-D-8``).

``GET /health`` and the two ``POST /v1/documents/{summary,pages}`` operations of the contract
(``contract.py``). The server itself never opens a PDF: it validates the request, resolves the
named file inside ``ELE_PARSE_STAGE_ROOT``, and hands the work to a fresh limited child
(``sandbox.py``). A body over 4 KiB is refused unread; a path that is not
``<uuid>/<uuid>.pdf``, resolves outside the root, is a symlink or is not a regular file is
``400``/``404``. At most ``ELE_PARSE_MAX_CHILDREN`` children run at once; the next request is
``503 busy`` with ``Retry-After``.

No secret, no DSN, no network client: the service is configured by five non-secret variables
and reads one read-only volume (DEC-04).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from typing import Any, Final

from services.ele_parse import SERVICE_VERSION, contract, sandbox

PYMUPDF_VERSION_KEY: Final = "pymupdf_version"
RETRY_AFTER_S: Final = 5
SOCKET_TIMEOUT_S: Final = 30.0


@dataclass(frozen=True, slots=True)
class Config:
    stage_root: Path
    port: int = 8090
    page_timeout_s: float = 10.0
    range_timeout_s: float = 300.0
    memory_mb: int = 1024
    max_children: int = 2
    token_sample: int = 200

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        return cls(
            stage_root=Path(source.get("ELE_PARSE_STAGE_ROOT", "/var/lib/aizzak/ele-stage")),
            port=int(source.get("ELE_PARSE_PORT", "8090")),
            page_timeout_s=float(source.get("ELE_PARSE_PAGE_TIMEOUT_S", "10")),
            range_timeout_s=float(source.get("ELE_PARSE_RANGE_TIMEOUT_S", "300")),
            memory_mb=int(source.get("ELE_PARSE_MEMORY_MB", "1024")),
            max_children=int(source.get("ELE_PARSE_MAX_CHILDREN", "2")),
            token_sample=int(source.get("ELE_PARSE_TOKEN_SAMPLE", "200")),
        )

    def timeout_for(self, pages: int) -> float:
        """``page_timeout_s x pages``, capped by the range ceiling."""
        return min(self.page_timeout_s * pages, self.range_timeout_s)


def resolve(stage_root: Path, name: str) -> Path:
    """The staged file ``name`` names, or ``ContractError`` 400/404. The name was already shaped
    by ``contract.parse_file``; this proves the PATH: inside the root, not a link, a regular
    file."""
    root = stage_root.resolve()
    candidate = stage_root / name
    if candidate.is_symlink() or candidate.parent.is_symlink():
        raise contract.ContractError(400, "bad_path", "a symbolic link is not a staged file")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise contract.ContractError(400, "bad_path", "the path leaves the stage root")
    if not resolved.is_file():
        raise contract.ContractError(404, "not_found", "no such staged file")
    return resolved


class EleParseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: Config) -> None:
        super().__init__(address, _Handler)
        self.config = config
        self.children = threading.BoundedSemaphore(config.max_children)


def make_server(config: Config, host: str = "0.0.0.0") -> EleParseServer:
    return EleParseServer((host, config.port), config)


class _Handler(BaseHTTPRequestHandler):
    server: EleParseServer
    protocol_version = "HTTP/1.1"
    server_version = "ele-parse"
    sys_version = ""
    # A client that connects and goes quiet must not park a thread forever (QA-2). The timeout
    # covers reading the request; the child's own deadline covers the work.
    timeout = SOCKET_TIMEOUT_S

    # -- routing -----------------------------------------------------------------
    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"code": "not_found"})
            return
        version = _pymupdf_version()
        self._send(
            200,
            {
                "status": "ok",
                "service_version": SERVICE_VERSION,
                PYMUPDF_VERSION_KEY: version,
                "schema": contract.SCHEMA,
            },
        )

    def do_POST(self) -> None:
        try:
            body = self._read_json()
            if self.path == "/v1/documents/summary":
                name = contract.parse_file(body)
                self._send(
                    200, self._run(name, {"op": "summary"}, self.server.config.page_timeout_s)
                )
            elif self.path == "/v1/documents/pages":
                request = contract.parse_pages(body)
                pages = request.last_page - request.first_page + 1
                self._send(
                    200,
                    self._run(
                        request.file,
                        {
                            "op": "pages",
                            "first": request.first_page,
                            "last": request.last_page,
                            "token_sample": self.server.config.token_sample,
                        },
                        self.server.config.timeout_for(pages),
                    ),
                )
            else:
                self._send(404, {"code": "not_found"})
        except contract.ContractError as exc:
            headers = (
                {"Retry-After": str(RETRY_AFTER_S)}
                if exc.status == HTTPStatus.SERVICE_UNAVAILABLE
                else {}
            )
            self._send(exc.status, {"code": exc.code, "detail": exc.detail}, headers)

    # -- the work ------------------------------------------------------------------
    def _run(self, name: str, request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        config = self.server.config
        path = resolve(config.stage_root, name)
        if not self.server.children.acquire(blocking=False):
            raise contract.ContractError(503, "busy", "all inspection slots are in use")
        try:
            limits = sandbox.Limits(config.memory_mb, sandbox.cpu_seconds_for(timeout_s))
            return sandbox.run_child(
                {**request, "path": str(path)}, timeout_s=timeout_s, limits=limits
            )
        finally:
            self.server.children.release()

    # -- http plumbing -------------------------------------------------------------
    def _read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise contract.ContractError(400, "bad_request", "bad Content-Length") from None
        if length < 0 or length > contract.MAX_BODY_BYTES:
            self.close_connection = True  # the body is not read: do not reuse the connection
            raise contract.ContractError(
                400, "bad_request", f"the body is at most {contract.MAX_BODY_BYTES} bytes"
            )
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except ValueError:
            raise contract.ContractError(400, "bad_request", "the body is not JSON") from None

    def _send(
        self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status, HTTPStatus(status).phrase)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Access lines carry method, path and status only: a path is a pair of UUIDs, never
        drawing text."""
        # ``command`` / ``path`` are not set when the request line never parsed (an idle
        # connection that timed out, a malformed line), and this is then called to log the error.
        command = getattr(self, "command", None) or "-"
        path = (getattr(self, "path", None) or "-").split("?")[0]
        status = args[1] if len(args) > 1 else ""
        print(f"ele-parse {command} {path} {status}", flush=True)


def _pymupdf_version() -> str:
    """PyMuPDF's version without importing it here (the server stays free of the library)."""
    try:
        return metadata.version("pymupdf")
    except metadata.PackageNotFoundError:
        return "unknown"
