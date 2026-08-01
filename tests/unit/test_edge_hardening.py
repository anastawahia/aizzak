"""The edge (nginx) must actually enforce a rate limit, security headers, and
a per-host-gated HSTS setting -- and the TWO deployment paths
(`deploy/nginx/{nginx.conf,app-locations.conf}` for Compose,
`deploy/runpod/nginx.conf` for the single-Pod image) must not drift apart on
any of it (P1-7, `docs/p1-hardening-plan.md` §3 step 9).

**The gap this closes.** Before this step, `server_tokens off` was the ONLY
hardening either edge config carried: no `limit_req`/`limit_conn` at all, so
nothing but the post-auth `usage` quota stood between the internet and the
agent loop's own auth/authz code; no `X-Content-Type-Options`,
`Referrer-Policy` or `X-Frame-Options`; and the HSTS line was prose describing
its own absence, not a working directive. This module would have failed on
that prior state with the exact assertions below -- no `ImportError`, no
collection error -- verified live by `git stash`ing the three nginx files and
re-running this module (docs/log/3.88.md).

**The trap this specifically guards against.** `/health` (a 10s healthcheck)
must carry NEITHER `limit_req` NOR `limit_conn` -- rate-limiting it starves
the probe itself, not an attacker, and made this exact container `unhealthy`
once already (§3.76). `/api/v1/ws` (a long-lived WebSocket) must carry
`limit_conn` but NEVER `limit_req` -- a persistent connection is not a
repeated request. `/` (which also carries SSE) must carry `limit_req`, and
that is safe only because `limit_req` gates request ADMISSION, not an
in-flight stream.

Same shape as `test_deploy_worker_default.py` (step 3) and
`test_role_provisioning_wiring.py` (step 8's review): two files claiming to
agree and nothing that checked.

**⭐ This module SHIPPED BROKEN once already, and a purely textual guard is
why it wasn't caught (coordinator's review of this step).** The first version
declared the three security headers in `http` and `Strict-Transport-Security`
alone inside the `:443 server` block. nginx inherits `add_header` from a
parent context into a child ONLY IF the child declares NONE of its own --
declaring even one in a child REPLACES the whole inherited set, it does not
merge with it. So `:443` -- the actual production path -- silently shipped
ZERO of the three security headers on every response, while `:80` shipped all
three (verified live: a throwaway `nginx:1.27 --rm` container proxying the
broken config to a real backend returned all three headers on `:80` and none
on `:443`). `deploy/runpod/nginx.conf` had the identical shape with only ONE
server block, so it lost all three on every single request, not just some.

`test_security_headers_present_in_both_deployment_paths` below -- which only
checks that the header text appears SOMEWHERE in the file -- PASSED against
that broken config, because the text genuinely was there, just in a scope
that never reached a real response. A regex over the raw file cannot see
nginx's own inheritance rule; only a structural read of WHICH SCOPE a
directive lives in can. `test_no_scope_declares_a_partial_add_header_set`
below is what actually enforces the invariant the fix depends on (every scope
that declares `add_header` at all declares the FULL set, so no scope can ever
silently own a subset that overrides a parent's larger set); the textual test
is kept only as a cheap, necessary-but-NOT-sufficient sanity check, and its
own docstring says so.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_NGINX = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_COMPOSE_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"
_RUNPOD_NGINX = _REPO_ROOT / "deploy" / "runpod" / "nginx.conf"

_API_SPEC = _REPO_ROOT / "docs" / "design" / "03-api-spec.md"
_REQUIREMENTS = _REPO_ROOT / "docs" / "Requirements-v1.md"

_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("X-Frame-Options", "DENY"),
)

_LIMIT_REQ_ZONE_RE = re.compile(
    r"limit_req_zone\s+\$binary_remote_addr\s+zone=(\w+):(\w+)\s+rate=([\w/]+);"
)
_LIMIT_CONN_ZONE_RE = re.compile(r"limit_conn_zone\s+\$binary_remote_addr\s+zone=(\w+):(\w+);")
_HSTS_MAP_RE = re.compile(r'map\s+\$host\s+\$hsts_header\s*\{\s*default\s+"";', re.DOTALL)
_HSTS_GATED_ADD_HEADER_RE = re.compile(
    r"add_header\s+Strict-Transport-Security\s+\$hsts_header\s+always;"
)
# A LITERAL, unconditional HSTS value would defeat the whole per-host gate --
# guard against that regression specifically, not just against the gated form
# being absent.
_HSTS_LITERAL_RE = re.compile(r'add_header\s+Strict-Transport-Security\s+"max-age')


def _text(path: Path) -> str:
    assert path.is_file(), f"expected file not found: {path}"
    return path.read_text(encoding="utf-8")


def _locations(text: str) -> dict[str, str]:
    """Map location path -> body text. None of this repo's location blocks
    nest braces, so a non-greedy match to the next `\\n}` is exact."""
    return dict(re.findall(r"location\s+(\S+)\s*\{(.*?)\n\s*\}", text, re.DOTALL))


def _compose_combined_text() -> str:
    """The Compose path's `add_header`/`map` directives are split across TWO
    files by construction (`nginx.conf`'s `map $host $hsts_header` -- `map` is
    only legal in `http` -- and app-locations.conf's actual `add_header`
    lines, included at `server` level by both :80 and :443). A textual
    existence check that only reads one of the two would miss the other
    entirely; concatenating them is correct for "does this text exist
    ANYWHERE in the Compose path" -- it is NOT a substitute for the
    structural, scope-aware check below."""
    return _text(_COMPOSE_NGINX) + "\n" + _text(_COMPOSE_LOCATIONS)


# ── A real (if minimal) nginx-config parser ─────────────────────────────────
# Built specifically to answer the question a regex over raw text cannot: for
# a given SCOPE (block), which `add_header` directives does it declare
# DIRECTLY (not inherited, not from a nested child)? That is exactly what
# nginx's own inheritance rule keys off, and exactly what let the broken
# config above pass a text-only guard.

Directive = tuple[str, str, list[str]]  # ("directive", name, args)
Block = tuple[str, str, list[str], list["Statement"]]  # ("block", name, args, body)
Statement = Directive | Block


def _tokenize(text: str) -> list[str]:
    """Split on whitespace/`{`/`}`/`;`, respecting quoted strings (the HSTS
    value `"max-age=...; includeSubDomains"` contains a literal `;` INSIDE
    quotes -- treating that as a statement terminator would corrupt every
    block that follows) and stripping `#` comments to end-of-line."""
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            tokens.append(c)
            i += 1
        elif c in "\"'":
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                j += 1
            tokens.append(text[i : j + 1])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n{};#\"'":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse(tokens: list[str], pos: int = 0) -> tuple[list[Statement], int]:
    statements: list[Statement] = []
    while pos < len(tokens):
        if tokens[pos] == "}":
            return statements, pos + 1
        words: list[str] = []
        while pos < len(tokens) and tokens[pos] not in (";", "{"):
            words.append(tokens[pos])
            pos += 1
        if pos >= len(tokens) or not words:
            break
        name, args = words[0], words[1:]
        if tokens[pos] == ";":
            statements.append(("directive", name, args))
            pos += 1
        else:  # "{"
            body, pos = _parse(tokens, pos + 1)
            statements.append(("block", name, args, body))
    return statements, pos


def _parse_config(text: str) -> list[Statement]:
    statements, _ = _parse(_tokenize(text))
    return statements


def _splice_app_locations(
    statements: list[Statement], app_locations_body: list[Statement]
) -> list[Statement]:
    """Simulate nginx's own `include` by replacing
    `include /etc/nginx/app-locations.conf;` with that file's parsed
    top-level statements, wherever it appears -- so a block's "direct"
    children, post-splice, match what nginx itself actually sees inside that
    `server {}`."""
    result: list[Statement] = []
    for stmt in statements:
        if stmt[0] == "directive" and stmt[1] == "include":
            directive_args = stmt[2]
            target = directive_args[0].strip("\"'") if directive_args else ""
            if target.endswith("app-locations.conf"):
                result.extend(app_locations_body)
                continue
        if stmt[0] == "block":
            _, name, args, body = stmt
            result.append(("block", name, args, _splice_app_locations(body, app_locations_body)))
            continue
        result.append(stmt)
    return result


_REQUIRED_HEADER_NAMES = frozenset(
    {"X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options", "Strict-Transport-Security"}
)


def _own_add_header_names(body: list[Statement]) -> frozenset[str]:
    """The `add_header` header names this block declares DIRECTLY -- i.e. NOT
    inside a nested child block. This is precisely nginx's own inheritance
    unit: a block with ANY entry here stops inheriting its parent's
    `add_header` set entirely."""
    names: list[str] = []
    for stmt in body:
        if stmt[0] == "directive":
            _, name, args = stmt
            if name == "add_header" and args:
                names.append(args[0])
    return frozenset(names)


def _iter_blocks(statements: list[Statement]) -> list[tuple[str, list[str], list[Statement]]]:
    """Every block anywhere in the tree (recursively), as (name, args, body)."""
    found: list[tuple[str, list[str], list[Statement]]] = []
    for stmt in statements:
        if stmt[0] == "block":
            _, name, args, body = stmt
            found.append((name, args, body))
            found.extend(_iter_blocks(body))
    return found


def _partial_add_header_scopes(
    statements: list[Statement],
) -> list[tuple[str, list[str], frozenset[str]]]:
    """Every block that declares `add_header` directly but NOT the full
    required set -- i.e. every scope that would silently override an
    ancestor's headers with an incomplete set of its own."""
    violations = []
    for name, args, body in _iter_blocks(statements):
        own = _own_add_header_names(body)
        if own and not own.issuperset(_REQUIRED_HEADER_NAMES):
            violations.append((name, args, own))
    return violations


def test_both_zone_directives_are_findable_and_identical() -> None:
    """A regex that silently matches nothing would pass forever while
    checking nothing (the 3.69 lesson, restated in every wiring guard in this
    repo) -- so require a match before comparing."""
    compose_req = _LIMIT_REQ_ZONE_RE.search(_text(_COMPOSE_NGINX))
    runpod_req = _LIMIT_REQ_ZONE_RE.search(_text(_RUNPOD_NGINX))
    assert compose_req is not None, (
        f"{_COMPOSE_NGINX}: no `limit_req_zone $binary_remote_addr zone=...` line found"
    )
    assert runpod_req is not None, (
        f"{_RUNPOD_NGINX}: no `limit_req_zone $binary_remote_addr zone=...` line found"
    )
    assert compose_req.groups() == runpod_req.groups(), (
        f"limit_req_zone drifted between the two deployment paths: "
        f"{_COMPOSE_NGINX.name}={compose_req.groups()!r} vs "
        f"{_RUNPOD_NGINX.name}={runpod_req.groups()!r}"
    )

    compose_conn = _LIMIT_CONN_ZONE_RE.search(_text(_COMPOSE_NGINX))
    runpod_conn = _LIMIT_CONN_ZONE_RE.search(_text(_RUNPOD_NGINX))
    assert compose_conn is not None, (
        f"{_COMPOSE_NGINX}: no `limit_conn_zone $binary_remote_addr zone=...` line found"
    )
    assert runpod_conn is not None, (
        f"{_RUNPOD_NGINX}: no `limit_conn_zone $binary_remote_addr zone=...` line found"
    )
    assert compose_conn.groups() == runpod_conn.groups(), (
        f"limit_conn_zone drifted between the two deployment paths: "
        f"{_COMPOSE_NGINX.name}={compose_conn.groups()!r} vs "
        f"{_RUNPOD_NGINX.name}={runpod_conn.groups()!r}"
    )


def test_health_carries_neither_limit_req_nor_limit_conn() -> None:
    for path, text in (
        (_COMPOSE_LOCATIONS, _text(_COMPOSE_LOCATIONS)),
        (_RUNPOD_NGINX, _text(_RUNPOD_NGINX)),
    ):
        locations = _locations(text)
        assert "/health" in locations, f"{path}: no `location /health {{...}}` block found"
        body = locations["/health"]
        assert "limit_req" not in body, (
            f"{path}: `/health` must never carry `limit_req` -- it starves the "
            "10s orchestrator healthcheck, not an attacker (§3.76's `unhealthy`-at-504 lesson)"
        )
        assert "limit_conn" not in body, f"{path}: `/health` must never carry `limit_conn` either"


def test_ws_carries_limit_conn_but_never_limit_req() -> None:
    for path, text in (
        (_COMPOSE_LOCATIONS, _text(_COMPOSE_LOCATIONS)),
        (_RUNPOD_NGINX, _text(_RUNPOD_NGINX)),
    ):
        locations = _locations(text)
        assert "/api/v1/ws" in locations, f"{path}: no `location /api/v1/ws {{...}}` block found"
        body = locations["/api/v1/ws"]
        assert "limit_conn" in body, (
            f"{path}: `/api/v1/ws` should bound concurrent connections per IP (`limit_conn`)"
        )
        assert "limit_req" not in body, (
            f"{path}: `/api/v1/ws` must never carry `limit_req` -- a WebSocket handshake "
            "is one request, then the socket stays open; a long-lived connection is "
            "not a repeated request"
        )


def test_root_location_carries_limit_req() -> None:
    for path, text in (
        (_COMPOSE_LOCATIONS, _text(_COMPOSE_LOCATIONS)),
        (_RUNPOD_NGINX, _text(_RUNPOD_NGINX)),
    ):
        locations = _locations(text)
        assert "/" in locations, f"{path}: no `location / {{...}}` block found"
        body = locations["/"]
        assert "limit_req" in body, (
            f"{path}: `location /` (the auth/authz path, and SSE) has no `limit_req` -- "
            "this is the exact gap P1-7 exists to close"
        )


def test_metrics_is_blocked_at_the_edge_in_both_deployment_paths() -> None:
    """P1-3's own mzalaq #2 (`docs/p1-hardening-plan.md` §3 step 10):
    `/metrics` must never be reachable through this edge -- Prometheus text
    about the Outbox/DLQ internals is operator information, the SAME
    `expose`-not-`ports` trust boundary the embedding service already draws
    (`docker-compose.yml`, `release-blockers-plan.md`'s rejected-port
    precedent, n-3). `location /` proxies everything it does not itself
    intercept, so this has to be an explicit location of its own -- and it
    must refuse LOCALLY (`return 404;`), never `proxy_pass` to the app at
    all, or a scraper reaching this edge would see it just like any other
    path."""
    for path, text in (
        (_COMPOSE_LOCATIONS, _text(_COMPOSE_LOCATIONS)),
        (_RUNPOD_NGINX, _text(_RUNPOD_NGINX)),
    ):
        locations = _locations(text)
        assert "/metrics" in locations, f"{path}: no `location /metrics {{...}}` block found"
        body = locations["/metrics"]
        assert "proxy_pass" not in body, (
            f"{path}: `/metrics` must be answered locally, never proxied to the app -- "
            "this location exists specifically so a scraper reaching this edge cannot "
            "see it at all"
        )
        assert "return 404" in body, f"{path}: `/metrics` should refuse with a plain 404"


def test_security_headers_present_in_both_deployment_paths() -> None:
    """NECESSARY, but proven NOT SUFFICIENT -- keep reading before trusting a
    green result here. This only checks that the header text appears
    SOMEWHERE in the file; it says nothing about which nginx SCOPE it lives
    in. The first version of this fix passed this exact test while shipping
    zero of these three headers on `:443` (the actual production path) --
    see the module docstring and `test_no_scope_declares_a_partial_add_header_set`
    below, which is what actually enforces the invariant this test cannot
    see. Reads the Compose path as `nginx.conf` + `app-locations.conf`
    combined (`_compose_combined_text`) -- the `add_header` lines live in the
    latter, not the former; see that helper's docstring for why."""
    for label, text in (
        ("deploy/nginx/{nginx.conf,app-locations.conf}", _compose_combined_text()),
        (_RUNPOD_NGINX, _text(_RUNPOD_NGINX)),
    ):
        for name, value in _SECURITY_HEADERS:
            pattern = re.compile(rf"add_header\s+{re.escape(name)}\s+{re.escape(value)}\s+always;")
            assert pattern.search(text), f"{label}: missing `add_header {name} {value} always;`"


def test_no_scope_declares_a_partial_add_header_set() -> None:
    """The REAL enforcement mechanism (coordinator's review of this step) --
    a structural read of the config, not a regex over its raw text.

    nginx inherits `add_header` from a parent context into a child ONLY IF
    the child declares NONE of its own -- declaring even one in a child
    REPLACES the whole inherited set, not merges with it. So the only way to
    be safe against that rule is to guarantee that EVERY scope which declares
    `add_header` at all declares the FULL required set (a superset is fine;
    a strict subset is exactly the bug: a scope silently overriding an
    ancestor's larger set with less). This walks the ACTUAL parsed config
    tree -- for the Compose path, with `include app-locations.conf;` spliced
    in exactly where nginx itself would read it, so a `server {}` block's
    "direct" children match what nginx really sees -- and flags every block
    that violates that invariant, naming the block and exactly which of the
    four headers it is missing.

    This is what the text-only test above cannot do: the broken first
    version of this config had the three headers as direct children of
    `http`, and `Strict-Transport-Security` alone as a direct child of the
    `:443 server` block -- two DIFFERENT scopes, neither a superset of the
    required four, both violations this function would have named."""
    compose_app_locations = _parse_config(_text(_COMPOSE_LOCATIONS))
    compose_tree = _splice_app_locations(
        _parse_config(_text(_COMPOSE_NGINX)), compose_app_locations
    )
    runpod_tree = _parse_config(_text(_RUNPOD_NGINX))

    for path, tree in ((_COMPOSE_NGINX, compose_tree), (_RUNPOD_NGINX, runpod_tree)):
        violations = _partial_add_header_scopes(tree)
        assert not violations, (
            f"{path}: found scope(s) declaring a PARTIAL `add_header` set -- each of these "
            "silently overrides any parent's headers with less, per nginx's own inheritance "
            f"rule (a child with ANY `add_header` inherits NONE from its parent): {violations!r}"
        )


def test_hsts_is_a_real_gated_setting_not_a_literal_or_a_comment() -> None:
    """Reads the Compose path combined (`_compose_combined_text`): the `map`
    itself is only legal in `nginx.conf`'s `http` block, while the
    `add_header` that consumes it lives in `app-locations.conf`."""
    for label, text in (
        ("deploy/nginx/{nginx.conf,app-locations.conf}", _compose_combined_text()),
        (_RUNPOD_NGINX, _text(_RUNPOD_NGINX)),
    ):
        assert _HSTS_MAP_RE.search(text), (
            f'{label}: no `map $host $hsts_header {{ default ""; ... }}` -- HSTS must be '
            "a real, per-host-gated setting (P1-7), not prose describing its own absence"
        )
        assert _HSTS_GATED_ADD_HEADER_RE.search(text), (
            f"{label}: no `add_header Strict-Transport-Security $hsts_header always;` -- "
            "the map exists but nothing consumes it"
        )
        assert not _HSTS_LITERAL_RE.search(text), (
            f"{label}: found a LITERAL, unconditional Strict-Transport-Security value -- "
            "this defeats the per-host gate and would poison `localhost` for every "
            "other project on the machine the moment this file is used for local dev (§3.76)"
        )


def test_no_cors_surface_exists_anywhere_under_deploy_or_src() -> None:
    """The decided-not-built half of P1-7: CORS is a deliberate non-feature
    (`03-api-spec.md §5`). Adding `CORSMiddleware` or an nginx CORS block
    "just in case" is exactly the surface that decision closes."""
    forbidden = ("Access-Control-Allow-Origin", "CORSMiddleware")
    for root in (_REPO_ROOT / "deploy", _REPO_ROOT / "src"):
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for token in forbidden:
                assert token not in text, (
                    f"{candidate}: contains {token!r} -- CORS is a deliberate non-feature "
                    "in v1 (03-api-spec.md §5); this is exactly the surface that closes"
                )


def test_cors_decision_is_documented_in_the_design_and_requirements_docs() -> None:
    api_spec_text = _text(_API_SPEC)
    assert "CORS" in api_spec_text and "Access-Control-Allow-Origin" in api_spec_text, (
        f"{_API_SPEC}: expected an explicit CORS section naming "
        "`Access-Control-Allow-Origin` and the same-origin assumption (P1-7)"
    )

    requirements_text = _text(_REQUIREMENTS)
    assert "CORS" in requirements_text, (
        f"{_REQUIREMENTS}: expected a security requirement row documenting the "
        "CORS decision (P1-7), cross-referencing 03-api-spec.md"
    )
