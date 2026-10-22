"""Edge guards for the presigned file URLs (server-gpu-plan.md 6.2).

On a server the app signs upload and download URLs for the public name
(``https://<domain>/workspace-files/<workspace>/<file>?X-Amz-...``), and
``deploy/nginx/app-locations.conf`` passes them to MinIO. These tests read the
Compose edge, the RunPod edge and ``docker-compose.yml`` structurally (the
parser in ``test_edge_hardening.py``, as ``test_edge_cloudflare.py`` does) and
each guard is also shown failing on an edited copy, so none passes vacuously.

No Docker and no network. What nginx really does with these locations is
measured by ``test_public_file_links_containers.py`` (opt-in).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits, MinioSettings
from app.infrastructure.storage.minio_storage import (
    MinioStorage,
    create_minio_client,
    create_minio_signing_client,
)
from tests.unit.test_edge_hardening import Statement, _iter_blocks, _parse_config
from tests.unit.test_files_use_cases import _SPACE, _ctx, _FakeFiles, _transfers

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_RUNPOD = _REPO_ROOT / "deploy" / "runpod" / "nginx.conf"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_CLOUDFLARE_FREE_BODY_LIMIT = 104_857_600  # 100 MB; Cloudflare answers 413 above it
_PREFIX = "/workspace-files/"
_FIVE_HIDDEN = frozenset(
    {
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-expose-headers",
        "strict-transport-security",
        "x-content-type-options",
    }
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _swap(text: str, old: str, new: str) -> str:
    assert old in text, f"the edited copy needs {old!r}, which is not in the file any more"
    return text.replace(old, new, 1)


def location_body(tree: list[Statement], *args: str) -> list[Statement]:
    """Exactly one ``location <args...>`` block anywhere in the tree."""
    found = [body for name, a, body in _iter_blocks(tree) if name == "location" and a == [*args]]
    assert len(found) == 1, f"expected exactly one `location {' '.join(args)}`, found {len(found)}"
    return found[0]


def direct(body: list[Statement], name: str) -> list[list[str]]:
    """The arguments of every ``name`` directive that is a direct child."""
    return [list(s[2]) for s in body if s[0] == "directive" and s[1] == name]


def size_bytes(value: str) -> int:
    """nginx sizes: ``50m`` -> 52_428_800 (k/m/g, any case)."""
    match = re.fullmatch(r"(\d+)([kmg]?)", value.strip().lower())
    assert match is not None, f"not an nginx size: {value!r}"
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2)]
    return int(match.group(1)) * scale


def _no_comments(text: str) -> str:
    return re.sub(r"#[^\n]*", "", text)


def _files_body(text: str) -> list[Statement]:
    return location_body(_parse_config(text), "^~", _PREFIX)


# --------------------------------------------------------------------------- #
# AC-1.4 -- the body ceiling                                                  #
# --------------------------------------------------------------------------- #
def body_limit_problems(locations_text: str, max_upload_bytes: int) -> list[str]:
    limits = direct(_files_body(locations_text), "client_max_body_size")
    if len(limits) != 1:
        return [f"expected one client_max_body_size in the files location, found {len(limits)}"]
    ceiling = size_bytes(limits[0][0])
    problems = []
    if ceiling != max_upload_bytes:
        problems.append(
            f"client_max_body_size {limits[0][0]} ({ceiling}) != max_upload_bytes "
            f"({max_upload_bytes}): raise both together"
        )
    if ceiling >= _CLOUDFLARE_FREE_BODY_LIMIT:
        problems.append(f"client_max_body_size {ceiling} is not under Cloudflare's 100 MB")
    return problems


def test_the_body_limit_is_the_app_s_upload_limit_and_under_cloudflare_s() -> None:
    assert body_limit_problems(_read(_LOCATIONS), Limits().max_upload_bytes) == []


def test_the_body_limit_guard_fails_when_one_number_moves_alone() -> None:
    real = _read(_LOCATIONS)
    app = Limits().max_upload_bytes
    assert app == 52_428_800
    assert body_limit_problems(
        _swap(real, "client_max_body_size    50m;", "client_max_body_size    51m;"), app
    )
    assert body_limit_problems(real, 60 * 1024 * 1024)
    over = _swap(real, "client_max_body_size    50m;", "client_max_body_size    200m;")
    problems = body_limit_problems(over, 209_715_200)
    assert len(problems) == 1 and "Cloudflare" in problems[0]


# --------------------------------------------------------------------------- #
# AC-1.8 -- the signed path is ASCII                                          #
# --------------------------------------------------------------------------- #
async def test_an_arabic_file_name_never_reaches_the_signed_path() -> None:
    settings = MinioSettings(
        endpoint="minio:9000", public_endpoint="api.aizzak.test", public_secure=True
    )
    client = create_minio_client(settings, access_key="ak", secret_key="sk")
    signing = create_minio_signing_client(settings, client, access_key="ak", secret_key="sk")
    storage = MinioStorage(client, settings.bucket, signing)
    workspace_id = new_uuid7()

    registered = await _transfers(_FakeFiles(), storage, minio=settings).register(
        _ctx(workspace_id),
        space_id=_SPACE,
        name="تقرير الربع الأوّل.pdf",
        content_type="application/pdf",
        size_bytes=2048,
    )

    parsed = urlparse(registered.upload_url)
    assert parsed.scheme == "https" and parsed.netloc == "api.aizzak.test"
    assert parsed.path == f"{_PREFIX}{workspace_id}/{registered.file.id}"
    assert registered.upload_url.isascii()
    assert "pdf" not in parsed.path
    assert unquote(registered.upload_url).isascii()
    prefix_args = [
        a
        for name, a, _ in _iter_blocks(_parse_config(_read(_LOCATIONS)))
        if name == "location" and a[:1] == ["^~"]
    ]
    assert [a[1] for a in prefix_args] == [_PREFIX]
    assert parsed.path.startswith(prefix_args[0][1])


# --------------------------------------------------------------------------- #
# FR-2, 3, 4, 8, 9, 10 -- the shape of the route                              #
# --------------------------------------------------------------------------- #
def _route_header_problems(body: list[Statement]) -> list[str]:
    problems: list[str] = []
    headers = {a[0].lower(): a[1:] for a in direct(body, "proxy_set_header") if a}
    if headers.get("host") != ["$http_host"]:
        problems.append("host: Host must be $http_host")
    if headers.get("x-forwarded-host") != ['""']:
        problems.append('x-forwarded-host: must be reset to ""')
    hidden = {a[0].lower() for a in direct(body, "proxy_hide_header")}
    if not hidden >= _FIVE_HIDDEN:
        problems.append(f"hide-header: missing {sorted(_FIVE_HIDDEN - hidden)}")
    return problems


def _route_destination_problems(body: list[Statement]) -> list[str]:
    problems: list[str] = []
    if ["$aizzak_minio", '"minio:9000"'] not in direct(body, "set"):
        problems.append('destination: `set $aizzak_minio "minio:9000";` is missing')
    if direct(body, "proxy_pass") != [["http://$aizzak_minio$request_uri"]]:
        problems.append("destination: proxy_pass must be http://$aizzak_minio$request_uri")
    return problems


def _route_global_problems(
    tree: list[Statement], locations_text: str, nginx_text: str
) -> list[str]:
    problems: list[str] = []
    minio_passes = [
        arg
        for _, _, b in _iter_blocks(tree)
        for arg in direct(b, "proxy_pass")
        if "minio" in " ".join(arg)
    ]
    if len(minio_passes) != 1:
        problems.append(f"other-minio-proxy: {len(minio_passes)} proxy_pass directives name minio")
    for forbidden in ("$http_x_forwarded_host", "$minio_signed_host"):
        for label, text in (("app-locations.conf", locations_text), ("nginx.conf", nginx_text)):
            if forbidden in _no_comments(text):
                problems.append(f"forbidden-variable: {label} reads {forbidden}")
    http = [b for n, _, b in _iter_blocks(_parse_config(nginx_text)) if n == "http"]
    if not http or not direct(http[0], "resolver"):
        problems.append("resolver: nginx.conf lost its `resolver` (per-request resolution)")
    return problems


def files_route_problems(locations_text: str, nginx_text: str) -> list[str]:
    problems: list[str] = []
    tree = _parse_config(locations_text)
    exact = [b for n, a, b in _iter_blocks(tree) if n == "location" and a == ["=", _PREFIX]]
    prefixed = [b for n, a, b in _iter_blocks(tree) if n == "location" and a == ["^~", _PREFIX]]
    if len(exact) != 1:
        problems.append("exact-root: need exactly one `location = /workspace-files/`")
    elif exact[0] != [("directive", "return", ["404"])]:
        problems.append("exact-root: its body must be exactly `return 404`")
    if len(prefixed) != 1:
        return [*problems, "prefix: need exactly one `location ^~ /workspace-files/`"]
    body = prefixed[0]
    for scope, name in ((exact[0] if exact else [], "exact-root"), (body, "prefix")):
        if direct(scope, "add_header"):
            problems.append(f"add-header: {name} location declares add_header (wipes the set)")
    methods = [(n, a, b) for n, a, b in _iter_blocks(body) if n == "limit_except"]
    if methods != [("limit_except", ["GET", "PUT"], [("directive", "deny", ["all"])])]:
        problems.append("methods: need exactly `limit_except GET PUT { deny all; }`")
    return [
        *problems,
        *_route_destination_problems(body),
        *_route_header_problems(body),
        *_route_global_problems(tree, locations_text, nginx_text),
    ]


def test_the_files_route_has_the_decided_shape() -> None:
    assert files_route_problems(_read(_LOCATIONS), _read(_NGINX)) == []


def test_the_files_route_guard_fails_on_each_regression() -> None:
    loc, ngx = _read(_LOCATIONS), _read(_NGINX)
    cases = {
        "exact-root": (
            _swap(
                loc,
                "location = /workspace-files/ {\n    return 404;",
                "location = /workspace-files/ {\n    return 403;",
            ),
            ngx,
        ),
        "prefix": (
            _swap(loc, "location ^~ /workspace-files/ {", "location /workspace-files/ {"),
            ngx,
        ),
        "destination": (
            _swap(loc, 'set $aizzak_minio "minio:9000";', 'set $aizzak_minio "minio:9001";'),
            ngx,
        ),
        "add-header": (
            _swap(
                loc,
                "    proxy_buffering         off;",
                "    proxy_buffering         off;\n    add_header X-T 1;",
            ),
            ngx,
        ),
        "methods": (_swap(loc, "limit_except GET PUT {", "limit_except GET PUT POST {"), ngx),
        "host": (
            _swap(
                loc,
                "proxy_set_header Host              $http_host;",
                "proxy_set_header Host              $host;",
            ),
            ngx,
        ),
        "x-forwarded-host": (
            _swap(
                loc,
                'proxy_set_header X-Forwarded-Host  "";',
                "proxy_set_header X-Forwarded-Host  $http_x_forwarded_host;",
            ),
            ngx,
        ),
        "hide-header": (_swap(loc, "    proxy_hide_header Strict-Transport-Security;\n", ""), ngx),
        "other-minio-proxy": (
            _swap(
                loc, "location /health {", "location /health {\n    proxy_pass http://minio:9000;"
            ),
            ngx,
        ),
        "forbidden-variable": (loc, ngx + "\nmap $http_x_forwarded_host $x { default 1; }\n"),
        "resolver": (loc, re.sub(r"(?m)^\s*resolver\s+[^;]+;", "", ngx)),
    }
    for rule, (loc_text, ngx_text) in cases.items():
        problems = files_route_problems(loc_text, ngx_text)
        assert any(p.startswith(rule) for p in problems), f"{rule}: not caught, got {problems}"


# --------------------------------------------------------------------------- #
# AC-3.2 -- the two edges differ exactly where the owner decided              #
# --------------------------------------------------------------------------- #
_IDENTICAL = (
    "client_max_body_size",
    "proxy_request_buffering",
    "proxy_buffering",
    "proxy_read_timeout",
    "proxy_send_timeout",
    "limit_req",
)
_HANDLED = {*_IDENTICAL, "proxy_pass", "set", "proxy_set_header", "proxy_hide_header"}


def files_drift_problems(compose_locations_text: str, runpod_text: str) -> list[str]:
    problems: list[str] = []
    compose = _files_body(compose_locations_text)
    runpod = _files_body(runpod_text)
    for name in _IDENTICAL:
        if sorted(direct(compose, name)) != sorted(direct(runpod, name)):
            problems.append(f"{name} differs between the Compose and RunPod files locations")

    def names(body: list[Statement], directive: str) -> set[str]:
        return {a[0].lower() for a in direct(body, directive) if a}

    set_c, set_r = names(compose, "proxy_set_header"), names(runpod, "proxy_set_header")
    if set_c - set_r != {"x-forwarded-host"} or set_r - set_c:
        problems.append(
            f"proxy_set_header names differ beyond X-Forwarded-Host: "
            f"compose-only {sorted(set_c - set_r)}, runpod-only {sorted(set_r - set_c)}"
        )
    values_c = {a[0].lower(): a[1:] for a in direct(compose, "proxy_set_header") if a}
    values_r = {a[0].lower(): a[1:] for a in direct(runpod, "proxy_set_header") if a}
    if values_c.get("host") != ["$http_host"] or values_r.get("host") != ["$minio_signed_host"]:
        problems.append("Host must be $http_host on Compose and $minio_signed_host on RunPod")
    if values_c.get("x-forwarded-proto") != ["$scheme"] or values_r.get("x-forwarded-proto") != [
        "https"
    ]:
        problems.append("X-Forwarded-Proto must be $scheme on Compose and https on RunPod")
    hide_c, hide_r = names(compose, "proxy_hide_header"), names(runpod, "proxy_hide_header")
    if (
        hide_c - hide_r != {"strict-transport-security", "x-content-type-options"}
        or hide_r - hide_c
    ):
        problems.append("proxy_hide_header differs beyond HSTS and nosniff (Compose only)")
    return [
        *problems,
        *_structure_drift_problems(compose, runpod, compose_locations_text, runpod_text),
    ]


def _structure_drift_problems(
    compose: list[Statement], runpod: list[Statement], compose_text: str, runpod_text: str
) -> list[str]:
    problems: list[str] = []
    in_compose = {s[1] for s in compose if s[0] == "block"}
    in_runpod = {s[1] for s in runpod if s[0] == "block"}
    if in_compose != {"limit_except"} or in_runpod:
        problems.append("limit_except must exist on Compose only")
    if not (
        direct(compose, "set") == [["$aizzak_minio", '"minio:9000"']]
        and direct(compose, "proxy_pass") == [["http://$aizzak_minio$request_uri"]]
        and not direct(runpod, "set")
        and direct(runpod, "proxy_pass") == [["http://127.0.0.1:9000"]]
    ):
        problems.append("destination: Compose names minio per request, RunPod a literal loopback")
    exact_c = [1 for _, a, _ in _iter_blocks(_parse_config(compose_text)) if a == ["=", _PREFIX]]
    exact_r = [1 for _, a, _ in _iter_blocks(_parse_config(runpod_text)) if a == ["=", _PREFIX]]
    if len(exact_c) != 1 or exact_r:
        problems.append("the exact bucket-root location must exist on Compose only")
    for scope, other, label in ((compose, runpod, "Compose"), (runpod, compose, "RunPod")):
        for stmt in scope:
            if stmt[0] == "directive" and stmt[1] not in _HANDLED and not direct(other, stmt[1]):
                problems.append(f"{stmt[1]} is on the {label} files location only")
    return problems


def test_the_two_files_locations_differ_exactly_where_the_owner_decided() -> None:
    assert files_drift_problems(_read(_LOCATIONS), _read(_RUNPOD)) == []


def test_the_drift_guard_fails_when_either_side_is_unified() -> None:
    loc, run = _read(_LOCATIONS), _read(_RUNPOD)
    host = "proxy_set_header Host              $http_host;"
    assert files_drift_problems(
        _swap(loc, host, "proxy_set_header Host              $minio_signed_host;"), run
    )
    assert files_drift_problems(_swap(loc, '    proxy_set_header X-Forwarded-Host  "";\n', ""), run)
    assert files_drift_problems(
        loc,
        _swap(
            run,
            "proxy_pass http://127.0.0.1:9000;",
            "proxy_pass http://127.0.0.1:9000;\n            limit_except GET PUT { deny all; }",
        ),
    )
    assert files_drift_problems(
        _swap(loc, "client_max_body_size    50m;", "client_max_body_size    60m;"), run
    )
    assert files_drift_problems(
        _swap(
            loc,
            "    proxy_buffering         off;",
            "    proxy_buffering         off;\n    add_header X-T 1;",
        ),
        run,
    )


# --------------------------------------------------------------------------- #
# AC-5.5 -- MinIO stays on loopback                                           #
# --------------------------------------------------------------------------- #
def unbound_ports(service: Mapping[str, object]) -> list[str]:
    ports = service.get("ports") or []
    assert isinstance(ports, list)
    bad: list[str] = []
    for port in ports:
        if isinstance(port, str):
            if not port.startswith("127.0.0.1:"):
                bad.append(port)
        elif isinstance(port, dict):
            if port.get("host_ip") != "127.0.0.1":
                bad.append(str(port))
        else:
            bad.append(str(port))
    return bad


def test_minio_is_published_on_loopback_only() -> None:
    services = yaml.safe_load(_read(_COMPOSE))["services"]
    assert services["minio"].get("ports"), "minio publishes nothing: the guard reads nothing"
    assert unbound_ports(services["minio"]) == []


def test_the_loopback_guard_fails_on_a_port_published_everywhere() -> None:
    assert unbound_ports({"ports": ["${HOST_PORT_MINIO:-19000}:9000"]})
    assert unbound_ports({"ports": ["0.0.0.0:19000:9000"]})
    assert unbound_ports({"ports": [{"target": 9000, "published": 19000}]})
    assert not unbound_ports({"ports": ["127.0.0.1:19000:9000"]})
    assert not unbound_ports({"ports": [{"target": 9000, "host_ip": "127.0.0.1"}]})


# --------------------------------------------------------------------------- #
# AC-6.4 -- the route names the bucket                                        #
# --------------------------------------------------------------------------- #
def bucket_binding_problems(
    locations_text: str, runpod_text: str, compose_text: str, env_example_text: str
) -> list[str]:
    def first_segment(text: str) -> set[str]:
        return {
            a[1].strip("/")
            for n, a, _ in _iter_blocks(_parse_config(text))
            if n == "location" and len(a) == 2 and a[0] in ("=", "^~")
        }

    names: dict[str, set[str]] = {
        "app-locations.conf": first_segment(locations_text),
        "runpod/nginx.conf": first_segment(runpod_text),
        "docker-compose.yml": set(re.findall(r"\$\{MINIO_BUCKET:-([^}]+)\}", compose_text)),
        ".env.example": set(re.findall(r"(?m)^MINIO_BUCKET=(\S+)\s*$", env_example_text)),
    }
    problems = [f"{label} names no bucket" for label, found in names.items() if not found]
    every = set().union(*names.values())
    if len(every) > 1:
        problems.append(f"the bucket is named differently across files: {names}")
    return problems


def test_the_route_names_the_bucket_compose_and_env_example_default_to() -> None:
    problems = bucket_binding_problems(
        _read(_LOCATIONS), _read(_RUNPOD), _read(_COMPOSE), _read(_ENV_EXAMPLE)
    )
    assert problems == []
    assert MinioSettings().bucket == _PREFIX.strip("/")


def test_the_bucket_guard_fails_when_one_name_moves_alone() -> None:
    loc, run, comp, env = (_read(p) for p in (_LOCATIONS, _RUNPOD, _COMPOSE, _ENV_EXAMPLE))
    assert bucket_binding_problems(loc.replace("/workspace-files/", "/files/"), run, comp, env)
    assert bucket_binding_problems(
        loc, run, comp.replace("MINIO_BUCKET:-workspace-files", "MINIO_BUCKET:-x"), env
    )
    assert bucket_binding_problems(
        loc, run, comp, env.replace("MINIO_BUCKET=workspace-files", "MINIO_BUCKET=x")
    )
    assert bucket_binding_problems(loc, run.replace("/workspace-files/", "/files/"), comp, env)
