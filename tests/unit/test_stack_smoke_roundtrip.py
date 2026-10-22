"""Check [4] of deploy/smoke/stack_smoke.py: the public round trip (server-gpu-plan.md 6.2).

The script is not a package, so it is loaded by path. No network: httpx's
MockTransport stands in for the public address, and a fake storage for MinIO.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from app.framework.errors import NotFoundError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SMOKE = _REPO_ROOT / "deploy" / "smoke" / "stack_smoke.py"
_INSTALL = _REPO_ROOT / "deploy" / "release" / "install.sh"

_PUT_URL = (
    "https://api.aizzak.test/workspace-files/smoke/presign-put-probe.txt?X-Amz-Signature=sekret-put"
)
_GET_URL = (
    "https://api.aizzak.test/workspace-files/smoke/presign-put-probe.txt?X-Amz-Signature=sekret-get"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stack_smoke_under_test", _SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load()
_KEY = smoke._PROBE_KEY
_BODY = smoke._PROBE_BODY


class _Probe:
    """ProbeStorage fake; ``stored`` is what the mocked PUT wrote."""

    def __init__(self) -> None:
        self.stored: bytes | None = None
        self.deleted: list[str] = []
        self.presigned: list[str] = []

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        self.presigned.append("put")
        return _PUT_URL

    async def presign_get(self, key: str, ttl_s: int) -> str:
        self.presigned.append("get")
        return _GET_URL

    async def get(self, key: str) -> bytes:
        if self.stored is None:
            raise NotFoundError("object does not exist in storage")
        return self.stored

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


Handler = Callable[[httpx.Request, _Probe], httpx.Response]


def _client(handler: Handler, probe: _Probe, seen: list[tuple[str, str]]) -> httpx.AsyncClient:
    def route(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return handler(request, probe)

    return httpx.AsyncClient(transport=httpx.MockTransport(route))


def _good(request: httpx.Request, probe: _Probe) -> httpx.Response:
    if request.method == "PUT":
        probe.stored = request.content
        return httpx.Response(200)
    return httpx.Response(200, content=probe.stored or b"")


async def _run(
    handler: Handler, environ: dict[str, str] | None = None
) -> tuple[_Probe, list[tuple[str, str]], str | None]:
    """Run check [4] with the opt-in set; returns (probe, requests, SystemExit message)."""
    probe, seen = _Probe(), []
    try:
        await smoke.check_public_roundtrip(
            environ or {"SMOKE_PUBLIC_ROUNDTRIP": "1"},
            probe,
            lambda: _client(handler, probe, seen),
        )
    except SystemExit as exit_:
        return probe, seen, str(exit_.code)
    return probe, seen, None


# --------------------------------------------------------------------------- #
# AC-4.1                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, None),
        ({"RUNPOD_POD_ID": "x"}, "RUNPOD_POD_ID"),
        ({"SMOKE_PUBLIC_ROUNDTRIP": "1"}, "SMOKE_PUBLIC_ROUNDTRIP=1"),
        ({"RUNPOD_POD_ID": "x", "SMOKE_PUBLIC_ROUNDTRIP": "1"}, "RUNPOD_POD_ID"),
        ({"SMOKE_PUBLIC_ROUNDTRIP": ""}, None),
    ],
)
def test_the_round_trip_runs_on_runpod_or_on_request_and_is_skipped_otherwise(
    environ: dict[str, str], expected: str | None
) -> None:
    assert smoke.roundtrip_reason(environ) == expected


@pytest.mark.parametrize("value", ["true", "0", "yes", "2", " 1"])
def test_an_opt_in_value_other_than_1_stops_with_a_message(value: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        smoke.roundtrip_reason({"SMOKE_PUBLIC_ROUNDTRIP": value})
    assert "SMOKE_PUBLIC_ROUNDTRIP" in str(stopped.value.code)
    assert "must be 1 or unset" in str(stopped.value.code)


async def test_a_skipped_round_trip_prints_today_s_line_and_sends_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert smoke.SKIPPED_LINE == "[4] presigned PUT round trip: skipped (not on RunPod)"
    probe = _Probe()

    def forbidden() -> httpx.AsyncClient:
        raise AssertionError("no client may be opened when [4] is skipped")

    await smoke.check_public_roundtrip({}, probe, forbidden)

    assert capsys.readouterr().out == smoke.SKIPPED_LINE + "\n"
    assert probe.presigned == [] and probe.deleted == []


@pytest.mark.parametrize(
    "environ", [{"RUNPOD_POD_ID": "pod"}, {"SMOKE_PUBLIC_ROUNDTRIP": "1"}], ids=["runpod", "opt-in"]
)
async def test_runpod_and_the_opt_in_both_upload_then_download(
    environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    _, seen, failure = await _run(_good, environ)

    assert failure is None
    assert seen == [("PUT", _PUT_URL), ("GET", _GET_URL)]
    out = capsys.readouterr().out
    assert "[4] runs: " in out


def test_install_sh_runs_the_smoke_without_the_opt_in() -> None:
    install = _INSTALL.read_text(encoding="utf-8")
    assert "exec -T app python /app/deploy/smoke/stack_smoke.py" in install
    assert "SMOKE_PUBLIC_ROUNDTRIP" not in install


# --------------------------------------------------------------------------- #
# AC-4.3                                                                       #
# --------------------------------------------------------------------------- #
async def test_a_clean_round_trip_passes_and_deletes_the_probe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe, _, failure = await _run(_good)

    assert failure is None
    assert probe.deleted == [_KEY]
    out = capsys.readouterr().out
    assert "[4] presigned PUT through the public address stored the bytes  OK" in out
    assert "[4] presigned GET through the public address returned the same bytes  OK" in out


async def test_different_bytes_back_fail() -> None:
    def tampered(request: httpx.Request, probe: _Probe) -> httpx.Response:
        if request.method == "PUT":
            return _good(request, probe)
        return httpx.Response(200, content=b"<html>a login page</html>")

    probe, _, failure = await _run(tampered)

    assert failure is not None
    assert "differ from the" in failure and "downloads are broken" in failure
    assert probe.deleted == [_KEY]


_REFUSALS = [
    pytest.param(
        403,
        {},
        "<Error><Code>SignatureDoesNotMatch</Code></Error>",
        "SignatureDoesNotMatch",
        id="signature",
    ),
    pytest.param(
        413,
        {},
        "<html><title>413 Request Entity Too Large</title></html>",
        "client_max_body_size",
        id="413",
    ),
    pytest.param(
        526, {}, "<html>Invalid SSL certificate</html>", "Cloudflare could not use", id="526"
    ),
    pytest.param(
        403,
        {"cf-mitigated": "challenge"},
        "<html>Just a moment</html>",
        "cf-mitigated",
        id="challenge",
    ),
    pytest.param(
        404, {}, '{"type":"about:blank","status":404}', "does not route /workspace-files/", id="404"
    ),
    pytest.param(403, {}, "<html>403 Forbidden</html>", "refused by the edge", id="edge-403"),
    pytest.param(502, {}, "<html>502 Bad Gateway</html>", "could not reach MinIO", id="502"),
    pytest.param(418, {}, "teapot", "unexpected answer", id="other"),
]


@pytest.mark.parametrize(("status", "headers", "body", "hint"), _REFUSALS)
async def test_a_refused_put_names_its_status(
    status: int, headers: dict[str, str], body: str, hint: str
) -> None:
    def refuse(request: httpx.Request, probe: _Probe) -> httpx.Response:
        return httpx.Response(status, headers=headers, text=body)

    probe, seen, failure = await _run(refuse)

    assert failure is not None
    assert f"answered {status}" in failure and hint in failure
    assert "browser uploads are broken" in failure
    assert seen == [("PUT", _PUT_URL)]
    assert probe.deleted == [_KEY]


async def test_a_refused_get_names_its_status() -> None:
    def refuse_get(request: httpx.Request, probe: _Probe) -> httpx.Response:
        if request.method == "PUT":
            return _good(request, probe)
        return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")

    probe, _, failure = await _run(refuse_get)

    assert failure is not None
    assert "presigned GET" in failure and "answered 403" in failure
    assert "AccessDenied" in failure and "downloads are broken" in failure
    assert probe.deleted == [_KEY]


async def test_a_put_that_answers_200_without_storing_is_caught() -> None:
    def drops(request: httpx.Request, probe: _Probe) -> httpx.Response:
        return httpx.Response(200)

    probe, _, failure = await _run(drops)

    assert failure == "FAIL: presigned PUT answered 200 but the object is not in MinIO"
    assert probe.deleted == [_KEY]


async def test_an_unreachable_public_name_fails_with_the_error_class() -> None:
    def down(request: httpx.Request, probe: _Probe) -> httpx.Response:
        raise httpx.ConnectError("name not resolved")

    probe, _, failure = await _run(down)

    assert failure is not None
    assert "got no answer: ConnectError" in failure
    assert probe.deleted == [_KEY]


# --------------------------------------------------------------------------- #
# AC-4.3 -- nothing printed or raised carries the signature                    #
# --------------------------------------------------------------------------- #
async def test_no_line_or_message_carries_the_signature(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def leaky(request: httpx.Request, probe: _Probe) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    def refused(request: httpx.Request, probe: _Probe) -> httpx.Response:
        return httpx.Response(403, text="<Error><Code>SignatureDoesNotMatch</Code></Error>")

    def wrong(request: httpx.Request, probe: _Probe) -> httpx.Response:
        if request.method == "PUT":
            return _good(request, probe)
        return httpx.Response(200, content=b"other")

    for handler in (_good, leaky, refused, wrong):
        probe, _, failure = await _run(handler)
        printed = capsys.readouterr().out
        assert "sekret" not in printed and "sekret" not in (failure or "")
        assert "X-Amz-Signature=" not in printed
        assert probe.deleted == [_KEY]
