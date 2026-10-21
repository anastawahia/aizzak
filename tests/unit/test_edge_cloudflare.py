"""Edge guards for the Cloudflare-fronted deployment (server-gpu-plan.md 6.1).

``deploy/nginx/nginx.conf`` trusts ``CF-Connecting-IP`` for Cloudflare's
published ranges only, and sends HSTS for the API's own hostname only. These
tests read the file structurally (the parser in ``test_edge_hardening.py``,
which itself stays untouched) and each guard is also shown failing on an
edited copy, so none of them can pass vacuously.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path

from tests.unit.test_edge_hardening import Block, Directive, _parse_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"

_FORBIDDEN_OVERLAPS: tuple[IPv4Network | IPv6Network, ...] = tuple(
    ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
# No trusted range wider than /12 (v4) or /28 (v6). Cloudflare's widest today are /13 and /29;
# a wider one fails the build and gets a human look.
_WIDEST = {4: 12, 6: 28}

_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _text() -> str:
    return _NGINX.read_text(encoding="utf-8")


def _http_body(text: str) -> list[Directive | Block]:
    for stmt in _parse_config(text):
        if stmt[0] == "block" and stmt[1] == "http":
            return list(stmt[3])
    raise AssertionError("no http block")


def _http_directives(text: str, name: str) -> list[list[str]]:
    """Arguments of every `name` directive that is a DIRECT child of `http`."""
    return [list(s[2]) for s in _http_body(text) if s[0] == "directive" and s[1] == name]


def _anywhere(name: str) -> int:
    """How many times `name` is declared anywhere in both Compose-path files (comments stripped)."""

    return sum(
        _count_in(list(_parse_config(p.read_text(encoding="utf-8"))), name)
        for p in (_NGINX, _LOCATIONS)
    )


def _range_problems(ranges: list[str]) -> list[str]:
    problems: list[str] = []
    nets: list[IPv4Network | IPv6Network] = []
    for r in ranges:
        try:
            nets.append(ip_network(r, strict=True))
        except ValueError:
            problems.append(f"{r}: not a strict CIDR")
    if nets and not any(n.version == 4 for n in nets):
        problems.append("no IPv4 range")
    if nets and not any(n.version == 6 for n in nets):
        problems.append("no IPv6 range")
    for n in nets:
        if n.prefixlen < _WIDEST[n.version]:
            problems.append(f"{n}: wider than /{_WIDEST[n.version]}")
        for bad in _FORBIDDEN_OVERLAPS:
            if bad.version == n.version and n.overlaps(bad):
                problems.append(f"{n}: overlaps {bad}")
    return problems


def realip_problems(text: str) -> list[str]:
    """Everything wrong with the realip block of `text`; empty means good."""
    problems: list[str] = []
    ranges = [a[0] for a in _http_directives(text, "set_real_ip_from") if a]
    if not ranges:
        problems.append("no set_real_ip_from in http")
    if _http_directives(text, "real_ip_header") != [["CF-Connecting-IP"]]:
        problems.append("real_ip_header must be CF-Connecting-IP, once, in http")
    if _http_directives(text, "real_ip_recursive") != [["off"]]:
        problems.append("real_ip_recursive must be off, written out, in http")
    if len(ranges) != len(set(ranges)):
        problems.append("duplicate range")
    problems += _range_problems(ranges)
    # Any set_real_ip_from / real_ip_* that is not a direct child of http.
    for name in ("set_real_ip_from", "real_ip_header", "real_ip_recursive"):
        direct = len(_http_directives(text, name))
        everywhere = _count_in(_parse_config(text), name)
        if direct != everywhere:
            problems.append(f"{name} declared outside http")
    header = text.split("set_real_ip_from", 1)[0]
    if "api.cloudflare.com/client/v4/ips" not in header or not _DATE_RE.search(header):
        problems.append("the list does not name its source and copy date")
    return problems


def _count_in(stmts: list[Directive | Block], name: str) -> int:
    total = 0
    for s in stmts:
        if s[0] == "directive" and s[1] == name:
            total += 1
        elif s[0] == "block":
            total += _count_in(list(s[3]), name)
    return total


def hsts_entries(text: str) -> list[tuple[str, str]]:
    """(host, value) of the `map $host $hsts_header` block, in order."""
    for s in _http_body(text):
        if s[0] == "block" and s[1] == "map" and s[2] == ["$host", "$hsts_header"]:
            return [
                (str(d[1]).strip('"') if d[1] != "default" else "default", " ".join(d[2]))
                for d in s[3]
                if d[0] == "directive"
            ]
    raise AssertionError("no `map $host $hsts_header`")


# ── real client address (AC-1.1, FR-1..FR-3, NFR-8) ─────────────────────────


def test_cloudflare_s_header_is_the_only_real_ip_source_and_sits_in_http() -> None:
    text = _text()
    assert _http_directives(text, "real_ip_header") == [["CF-Connecting-IP"]]
    assert _anywhere("real_ip_header") == 1
    assert _http_directives(text, "real_ip_recursive") == [["off"]]
    assert _anywhere("set_real_ip_from") == len(_http_directives(text, "set_real_ip_from"))


def test_every_trusted_range_is_a_strict_cidr_and_both_families_are_there() -> None:
    ranges = [a[0] for a in _http_directives(_text(), "set_real_ip_from")]
    nets = [ip_network(r, strict=True) for r in ranges]
    assert {n.version for n in nets} == {4, 6}
    assert len(ranges) == len(set(ranges))
    assert len(nets) == 22


def test_no_trusted_range_is_private_loopback_docker_or_too_wide() -> None:
    problems = realip_problems(_text())
    assert problems == []


def test_the_list_names_its_source_and_the_day_it_was_copied() -> None:
    text = _text()
    header = text.split("set_real_ip_from", 1)[0]
    assert "https://api.cloudflare.com/client/v4/ips" in header
    assert _DATE_RE.search(header)
    assert "etag" in header


def test_the_real_ip_guard_fails_on_each_shape_it_exists_for() -> None:
    good = _text()
    assert realip_problems(good) == []

    first = "set_real_ip_from 173.245.48.0/20;"
    assert first in good
    edits = {
        "0.0.0.0/0": good.replace(first, "set_real_ip_from 0.0.0.0/0;"),
        "::/0": good.replace(first, "set_real_ip_from ::/0;"),
        "docker": good.replace(first, "set_real_ip_from 172.18.0.0/16;"),
        "bare loopback": good.replace(first, "set_real_ip_from 127.0.0.1;"),
        "recursive on": good.replace("real_ip_recursive off;", "real_ip_recursive on;"),
        "xff header": good.replace(
            "real_ip_header    CF-Connecting-IP;", "real_ip_header    X-Forwarded-For;"
        ),
        "non-strict": good.replace(first, "set_real_ip_from 173.245.48.1/20;"),
        "duplicate": good.replace(first, first + "\n    " + first),
    }
    for label, text in edits.items():
        assert text != good, label
        assert realip_problems(text), label

    no_ranges = re.sub(r"^\s*set_real_ip_from [^;]*;\n", "", good, flags=re.MULTILINE)
    assert realip_problems(no_ranges)

    inside_server = good.replace(
        "        server_name _;", "        server_name _;\n        set_real_ip_from 8.8.8.0/24;", 1
    )
    assert inside_server != good
    assert any("outside http" in p for p in realip_problems(inside_server))


# ── HSTS (AC-4.1, FR-16, FR-17) ──────────────────────────────────────────────


def hsts_problems(entries: list[tuple[str, str]]) -> list[str]:
    problems: list[str] = []
    if entries != [("default", '""'), ("api.aizzak.com", '"max-age=31536000"')]:
        problems.append(f"unexpected HSTS map: {entries}")
    for host, value in entries:
        if "includesubdomains" in value.lower() or "preload" in value.lower():
            problems.append(f"{host}: includeSubDomains/preload")
    return problems


def test_hsts_names_the_api_host_alone_without_subdomains_or_preload() -> None:
    entries = hsts_entries(_text())
    assert entries == [("default", '""'), ("api.aizzak.com", '"max-age=31536000"')]
    assert hsts_problems(entries) == []
    assert "deploy-linux-server" not in _text()


def test_the_hsts_guard_fails_on_a_second_host_includesubdomains_or_preload() -> None:
    good = _text()
    line = '"api.aizzak.com" "max-age=31536000";'
    assert line in good
    edits = [
        good.replace(line, line + '\n        "other.example" "max-age=31536000";'),
        good.replace(line, '"api.aizzak.com" "max-age=31536000; includeSubDomains";'),
        good.replace(line, '"api.aizzak.com" "max-age=31536000; preload";'),
        good.replace(line, ""),
    ]
    for text in edits:
        assert text != good
        assert hsts_problems(hsts_entries(text))
