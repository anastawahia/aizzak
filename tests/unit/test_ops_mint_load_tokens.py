"""Hermetic tests for the token-pool minter (capacity blocker ``د-9`` ·
``app/ops/mint_load_tokens.py``).

Firebase and the platform edge are both played by one ``httpx.MockTransport``
(the ``test_web_search.py`` precedent), dispatching on the request's host:
``identitytoolkit`` / ``securetoken`` answer as Firebase does -- including its
``error.message`` refusals -- and ``localhost`` answers as the edge does. The
ID tokens it hands out are real HS256 JWTs so ``verify`` reads real claims.

What is pinned here is the part a live run cannot check without spending the
quota: that a re-run creates nothing twice, that a quota refusal pauses the
lane once rather than failing, and that the pool file has exactly the shape
``lib/auth.js`` reads.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest

from app.ops import mint_load_tokens as module
from app.ops.mint_load_tokens import (
    QUOTA_RETRY_S,
    Account,
    QuotaExhausted,
    State,
    build_clients,
    delete_all,
    mint,
    refresh,
    verify_pool,
    write_includes,
    write_pool,
)

PROJECT = "aizzak-test"
KEY = "web-api-key"


_SERIAL = iter(range(1, 10**9))


def _id_token(uid: str, *, exp: int | None = None, aud: str = PROJECT) -> str:
    return jwt.encode(
        {
            "sub": uid,
            "serial": next(_SERIAL),  # a real issuer never repeats a token; neither does this
            "aud": aud,
            "iss": f"https://securetoken.google.com/{aud}",
            "email": f"{uid}@example.com",
            "iat": int(time.time()),
            "exp": exp if exp is not None else int(time.time()) + 3600,
        },
        "not-verified-here-and-thirty-two-bytes-long",
        algorithm="HS256",
    )


def _firebase_error(code: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": code}})


class FakeWorld:
    """Firebase and the edge, with the knobs each test turns."""

    def __init__(self) -> None:
        self.signups: list[str] = []
        self.refreshes: list[str] = []
        self.password_signins: list[str] = []
        self.deletes: list[str] = []
        self.contexts: list[str] = []
        self.spaces: list[str] = []
        self.quota_refusals_left = 0
        self.email_exists: set[str] = set()
        self.edge_429_left = 0
        self.expired_refresh_tokens: set[str] = set()
        self.seeded: set[str] = set()  # uids whose workspace the seed filled
        self.paginate_spaces = False
        self.listings: list[str] = []
        self.tokens: dict[str, str] = {}  # id_token -> uid
        self._n = 0

    def _issue(self, uid: str) -> str:
        token = _id_token(uid)
        self.tokens[token] = uid
        return token

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "identitytoolkit.googleapis.com":
            return self._identity(request)
        if host == "securetoken.googleapis.com":
            return self._secure_token(request)
        assert host == "localhost", host
        return self._edge(request)

    def _identity(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == KEY
        body = json.loads(request.content)
        if request.url.path.endswith(":signUp"):
            if self.quota_refusals_left:
                self.quota_refusals_left -= 1
                return _firebase_error("TOO_MANY_ATTEMPTS_TRY_LATER")
            if body["email"] in self.email_exists:
                return _firebase_error("EMAIL_EXISTS")
            self._n += 1
            uid = f"uid{self._n}"
            self.signups.append(body["email"])
            return httpx.Response(
                200,
                json={
                    "idToken": self._issue(uid),
                    "refreshToken": f"rt-{uid}",
                    "localId": uid,
                    "email": body["email"],
                    "expiresIn": "3600",
                },
            )
        if request.url.path.endswith(":signInWithPassword"):
            self.password_signins.append(body["email"])
            uid = "uid-" + body["email"].split("@")[0]
            return httpx.Response(
                200,
                json={"idToken": self._issue(uid), "refreshToken": f"rt2-{uid}", "localId": uid},
            )
        if request.url.path.endswith(":delete"):
            uid = self.tokens[body["idToken"]]
            self.deletes.append(uid)
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    def _secure_token(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == KEY
        form = dict(httpx.QueryParams(request.content.decode()))
        assert form["grant_type"] == "refresh_token"
        rt = form["refresh_token"]
        if rt in self.expired_refresh_tokens:
            return _firebase_error("TOKEN_EXPIRED")
        self.refreshes.append(rt)
        uid = rt.split("-", 1)[1]
        return httpx.Response(
            200, json={"id_token": self._issue(uid), "refresh_token": rt, "expires_in": "3600"}
        )

    def _edge(self, request: httpx.Request) -> httpx.Response:
        if self.edge_429_left:
            self.edge_429_left -= 1
            return httpx.Response(429, text="limited")
        token = request.headers["Authorization"].removeprefix("Bearer ")
        uid = self.tokens[token]
        if request.url.path == "/api/v1/me/context":
            self.contexts.append(uid)
            return httpx.Response(200, json={"workspace": {"id": f"ws-{uid}"}, "user": {}})
        if request.url.path == "/api/v1/spaces" and request.method == "POST":
            assert json.loads(request.content) == {"name": "load"}
            self.spaces.append(uid)
            return httpx.Response(201, json={"id": f"sp-{uid}", "name": "load"})
        if request.url.path == "/api/v1/spaces" and request.method == "GET":
            return self._list_spaces(request, uid)
        raise AssertionError(request.url)

    def _list_spaces(self, request: httpx.Request, uid: str) -> httpx.Response:
        """What the platform reports per space -- the tool's own ``load``
        (empty) and, once the seed ran, ``seed-space-0/1`` with content."""
        self.listings.append(uid)
        assert request.url.params["limit"] == "100"
        own = {"id": f"sp-{uid}", "name": "load", "file_count": 0, "conversation_count": 0}
        seeded = (
            [
                {
                    "id": f"seed-{uid}-1",
                    "name": "seed-space-1",
                    "file_count": 12,
                    "conversation_count": 3,
                },
                {
                    "id": f"seed-{uid}-0",
                    "name": "seed-space-0",
                    "file_count": 40,
                    "conversation_count": 7,
                },
            ]
            if uid in self.seeded
            else []
        )
        if self.paginate_spaces:
            cursor = request.url.params.get("cursor")
            if cursor is None:
                return httpx.Response(200, json={"data": [own], "meta": {"next_cursor": "c1"}})
            assert cursor == "c1"
            return httpx.Response(200, json={"data": seeded, "meta": {"next_cursor": None}})
        return httpx.Response(200, json={"data": [own, *seeded], "meta": {"next_cursor": None}})


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def world() -> FakeWorld:
    return FakeWorld()


def _clients(world: FakeWorld) -> module.Clients:
    return build_clients("https://localhost", transport=httpx.MockTransport(world.handler))


async def _mint(
    world: FakeWorld, state: State, tmp_path: Path, *, count: int, **kw: Any
) -> module.MintReport:
    clients = _clients(world)
    try:
        return await mint(
            state,
            count=count,
            api_key=KEY,
            clients=clients,
            state_path=tmp_path / "accounts.json",
            sleep=kw.pop("sleep", _Sleeps()),
            progress=kw.pop("progress", lambda _line: None),
            **kw,
        )
    finally:
        await clients.aclose()


# ── mint ──────────────────────────────────────────────────────────────────


async def test_mint_signs_up_provisions_and_writes_the_pool_auth_js_reads(
    world: FakeWorld, tmp_path: Path
) -> None:
    """Three calls per account in the order the platform forces -- the token
    buys the tenant, the tenant's id comes back in the same response, then
    the space -- and the pool file carries exactly ``workspace`` /
    ``space_id`` / ``id_token`` with ``stub`` declared false."""
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")
    report = await _mint(world, state, tmp_path, count=3)

    assert report.created == 3 and report.provisioned == 3 and report.failed == []
    assert world.signups == [f"load-t1-000{i}@example.com" for i in range(3)]
    assert sorted(world.contexts) == sorted(world.spaces) == ["uid1", "uid2", "uid3"]
    assert all(a.complete for a in state.accounts)

    pool_path = tmp_path / "tokens.json"
    assert write_pool(state, pool_path) == 3
    pool = json.loads(pool_path.read_text())
    assert pool["stub"] is False
    assert [sorted(t) for t in pool["tokens"]] == [
        ["id_token", "space_conversations", "space_files", "space_id", "space_name", "workspace"]
    ] * 3
    assert {(t["space_name"], t["space_files"]) for t in pool["tokens"]} == {("load", 0)}
    assert {t["workspace"] for t in pool["tokens"]} == {"ws-uid1", "ws-uid2", "ws-uid3"}
    assert {t["space_id"] for t in pool["tokens"]} == {"sp-uid1", "sp-uid2", "sp-uid3"}

    includes = tmp_path / "include.txt"
    assert write_includes(state, includes) == 3
    assert includes.read_text().split() == [a.workspace for a in state.accounts]


async def test_the_state_file_is_written_and_reloads_to_the_same_accounts(
    world: FakeWorld, tmp_path: Path
) -> None:
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")
    await _mint(world, state, tmp_path, count=2)
    reloaded = State.load(tmp_path / "accounts.json")
    assert reloaded.tag == "t1"
    assert [a.email for a in reloaded.accounts] == [a.email for a in state.accounts]
    assert all(a.password and a.refresh_token for a in reloaded.accounts)


async def test_a_rerun_creates_nothing_twice_and_finishes_a_half_provisioned_account(
    world: FakeWorld, tmp_path: Path
) -> None:
    """The quota makes resumption the normal case, not the exceptional one: a
    run that stopped at 100 continues at 101, and an account that signed up
    while the stack was down gets its tenant on the next run -- never a
    second sign-up, which would be a second tenant with the same email
    refused as ``EMAIL_EXISTS``."""
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")
    done = Account(
        ordinal=0,
        email=state.email_for(0),
        password="p",
        local_id="u0",
        refresh_token="rt-u0",
        id_token=world._issue("u0"),
        workspace="ws-u0",
        space_id="sp-u0",
    )
    half = Account(
        ordinal=1,
        email=state.email_for(1),
        password="p",
        local_id="u1",
        refresh_token="rt-u1",
        id_token=world._issue("u1"),
    )
    state.accounts = [done, half]

    report = await _mint(world, state, tmp_path, count=3)

    assert world.signups == [state.email_for(2)]  # only the missing ordinal
    assert sorted(world.contexts) == ["u1", "uid1"]  # the half-done one, and the new one
    assert report.created == 1 and report.provisioned == 2
    assert [a.ordinal for a in state.accounts] == [0, 1, 2]
    assert all(a.complete for a in state.accounts)


async def test_a_quota_refusal_pauses_the_lane_once_and_says_where_to_raise_it(
    world: FakeWorld, tmp_path: Path
) -> None:
    """One refusal, one wait, one message -- not eight workers each backing
    off. And the message names the number and the console page, because
    the tool cannot raise the quota and the operator can."""
    world.quota_refusals_left = 1
    sleeps = _Sleeps()
    lines: list[str] = []
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")

    report = await _mint(world, state, tmp_path, count=4, sleep=sleeps, progress=lines.append)

    assert report.failed == [] and len(world.signups) == 4
    assert sleeps.calls == [QUOTA_RETRY_S]
    quota_lines = [line for line in lines if "100 accounts/hour" in line]
    assert len(quota_lines) == 1
    assert "Sign-up quota" in quota_lines[0]


async def test_the_quota_wait_has_a_ceiling_and_stops_with_the_state_saved(
    world: FakeWorld, tmp_path: Path
) -> None:
    world.quota_refusals_left = 10**6
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")
    with pytest.raises(QuotaExhausted, match="0 accounts created"):
        await _mint(world, state, tmp_path, count=2, max_quota_wait_s=0)
    assert (tmp_path / "accounts.json").exists()


async def test_email_exists_is_reported_per_account_and_does_not_stop_the_others(
    world: FakeWorld, tmp_path: Path
) -> None:
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")
    world.email_exists = {state.email_for(1)}

    report = await _mint(world, state, tmp_path, count=3)

    assert report.created == 2
    assert len(report.failed) == 1 and "EMAIL_EXISTS" in report.failed[0]
    assert [a.ordinal for a in state.accounts] == [0, 2]


async def test_an_edge_429_is_a_pause_not_a_failure(world: FakeWorld, tmp_path: Path) -> None:
    """nginx meters per source address (د-8) and this tool runs from one;
    the limiter's refusal is retried, not reported as a broken stack."""
    world.edge_429_left = 2
    sleeps = _Sleeps()
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")

    report = await _mint(world, state, tmp_path, count=1, sleep=sleeps)

    assert report.failed == [] and state.accounts[0].complete
    assert len(sleeps.calls) == 2


async def test_the_edge_client_skips_tls_verification_and_the_firebase_client_does_not() -> None:
    """The self-signed edge (ح-19) is the harness's own ``-k``; a real key
    crossing to Google is not."""
    clients = build_clients("https://localhost/")
    try:
        assert str(clients.edge.base_url) == "https://localhost"
        # httpx keeps the verify decision in the transport's SSL context; the
        # observable difference is whether hostname checking is on.
        edge_ctx = clients.edge._transport._pool._ssl_context  # type: ignore[attr-defined]
        fb_ctx = clients.firebase._transport._pool._ssl_context  # type: ignore[attr-defined]
        assert edge_ctx.check_hostname is False
        assert fb_ctx.check_hostname is True
    finally:
        await clients.aclose()


# ── refresh / delete ──────────────────────────────────────────────────────


def _state_with(world: FakeWorld, n: int) -> State:
    state = State.new(base_url="https://localhost", email_domain="example.com", tag="t1")
    for i in range(n):
        uid = f"u{i}"
        state.accounts.append(
            Account(
                ordinal=i,
                email=state.email_for(i),
                password="p",
                local_id=uid,
                refresh_token=f"rt-{uid}",
                id_token=world._issue(uid),
                workspace=f"ws-{uid}",
                space_id=f"sp-{uid}",
            )
        )
    return state


async def test_refresh_exchanges_every_refresh_token_and_falls_back_to_the_password(
    world: FakeWorld,
) -> None:
    """Refresh tokens do not expire on a clock but CAN be revoked; the
    password is kept for exactly that case, so one revoked account does not
    cost the pool a re-mint (and the seed its ``--include-workspace`` ids)."""
    state = _state_with(world, 3)
    world.expired_refresh_tokens = {"rt-u1"}
    old_tokens = [a.id_token for a in state.accounts]
    clients = _clients(world)
    try:
        report = await refresh(state, api_key=KEY, clients=clients)
    finally:
        await clients.aclose()

    assert report.by_refresh == 2 and report.by_password == 1 and report.failed == []
    assert world.password_signins == [state.email_for(1)]
    assert all(a.id_token != old for a, old in zip(state.accounts, old_tokens, strict=True))
    assert state.accounts[1].refresh_token == "rt2-uid-load-t1-0001"  # rotated by sign-in
    assert all(a.workspace and a.space_id for a in state.accounts)  # never lost
    assert report.on_content == 0 and report.on_empty == 3  # nothing seeded yet


async def test_refresh_moves_each_entry_onto_the_space_the_seed_filled(
    world: FakeWorld, tmp_path: Path
) -> None:
    """The seed writes into ``seed-space-0/1``, never into ``load`` -- so an
    entry that kept ``load`` would send four scenarios out of five into an
    empty space. After the seed, ``refresh`` lists what each space holds and
    moves the entry onto the fullest; before it, ``load`` stays and the
    report says so. ``verify`` then refuses the un-moved pool."""
    state = _state_with(world, 3)
    world.seeded = {"u0", "u2"}
    world.paginate_spaces = True  # the listing is paged; the fullest may be on page two
    clients = _clients(world)
    try:
        report = await refresh(state, api_key=KEY, clients=clients)
    finally:
        await clients.aclose()

    assert report.failed == [] and report.on_content == 2 and report.on_empty == 1
    assert sorted(world.listings) == ["u0", "u0", "u1", "u1", "u2", "u2"]  # two pages each
    moved, kept, moved_too = state.accounts
    assert (moved.space_id, moved.space_name, moved.space_files) == (
        "seed-u0-0",
        "seed-space-0",
        40,
    )
    assert (moved_too.space_id, moved_too.space_conversations) == ("seed-u2-0", 7)
    assert (kept.space_id, kept.space_files, kept.space_conversations) == ("sp-u1", 0, 0)

    pool_path = tmp_path / "tokens.json"
    write_pool(state, pool_path)
    pool = json.loads(pool_path.read_text())
    verdicts = _verdicts(pool, ws_vus=9)
    assert verdicts["space content"] is False and verdicts["workspace+space_id"] is True

    world.seeded.add("u1")
    clients = _clients(world)
    try:
        await refresh(state, api_key=KEY, clients=clients)
    finally:
        await clients.aclose()
    write_pool(state, pool_path)
    assert _verdicts(json.loads(pool_path.read_text()), ws_vus=9)["space content"] is True


async def test_delete_renews_then_deletes_and_empties_the_state(world: FakeWorld) -> None:
    state = _state_with(world, 2)
    clients = _clients(world)
    try:
        report = await delete_all(state, api_key=KEY, clients=clients)
    finally:
        await clients.aclose()
    assert report.deleted == 2 and report.failed == []
    assert sorted(world.deletes) == ["u0", "u1"]
    assert state.accounts == []


# ── verify ────────────────────────────────────────────────────────────────


def _pool(n: int, **overrides: Any) -> dict[str, Any]:
    tokens = [
        {
            "workspace": f"ws-{i}",
            "space_id": f"sp-{i}",
            "id_token": _id_token(f"u{i}"),
            "space_files": 40,
            "space_conversations": 7,
        }
        for i in range(n)
    ]
    return {"stub": False, "tokens": tokens, **overrides}


def _verdicts(pool: dict[str, Any], **kw: Any) -> dict[str, bool]:
    args: dict[str, Any] = {
        "now": int(time.time()),
        "duration_s": 1800,
        "ws_vus": 1500,
        "project_id": PROJECT,
    }
    args.update(kw)
    return {c.name: c.ok for c in verify_pool(pool, **args)}


def test_a_full_pool_passes_every_check() -> None:
    assert all(_verdicts(_pool(500)).values())


def test_verify_mirrors_profile_js_three_sockets_per_user_guard() -> None:
    """``lib/profile.js`` refuses above 3 sockets per user; 1,500 sockets
    over 400 tokens is 3.75, refused there -- so refused here first."""
    verdicts = _verdicts(_pool(400))
    assert verdicts["tokens"] is False and verdicts["ws per user"] is False
    assert _verdicts(_pool(400), ws_vus=1200)["ws per user"] is True


def test_verify_refuses_a_stub_pool_a_short_lived_token_and_a_foreign_project() -> None:
    assert _verdicts(_pool(500, stub=True))["stub"] is False

    short = _pool(500)
    short["tokens"][7]["id_token"] = _id_token("u7", exp=int(time.time()) + 100)
    assert _verdicts(short)["earliest exp"] is False  # the EARLIEST governs

    foreign = _pool(500)
    foreign["tokens"][0]["id_token"] = _id_token("u0", aud="someone-elses-project")
    verdicts = _verdicts(foreign)
    assert verdicts["aud == FIREBASE_PROJECT_ID"] is False and verdicts["iss"] is False


def test_verify_refuses_a_missing_space_id_and_a_shared_tenant() -> None:
    pool = _pool(500)
    del pool["tokens"][3]["space_id"]
    pool["tokens"][4]["workspace"] = pool["tokens"][5]["workspace"]
    verdicts = _verdicts(pool)
    assert verdicts["workspace+space_id"] is False
    assert verdicts["distinct tenants"] is False


def test_verify_refuses_a_pool_whose_spaces_hold_nothing() -> None:
    """A pool minted before the seed, or written by an older tool without
    the fields, points at empty spaces either way -- and says what to do."""
    unmoved = _pool(500)
    for t in unmoved["tokens"][:3]:
        t["space_files"], t["space_conversations"] = 0, 0
    for t in unmoved["tokens"][3:]:
        del t["space_files"], t["space_conversations"]
    checks = {
        c.name: c
        for c in verify_pool(
            unmoved, now=int(time.time()), duration_s=1800, ws_vus=1500, project_id=PROJECT
        )
    }
    assert checks["space content"].ok is False
    assert (
        "0 of 500" in checks["space content"].detail
        and "seed first" in checks["space content"].detail
    )


def test_verify_without_a_project_id_reports_aud_rather_than_asserting_it() -> None:
    names = {c.name for c in verify_pool(_pool(1), now=0, duration_s=1, ws_vus=1, project_id=None)}
    assert "aud" in names and "aud == FIREBASE_PROJECT_ID" not in names


# ── Firebase error parsing, CLI refusals ──────────────────────────────────


def test_firebase_codes_are_read_from_error_message_with_the_human_suffix_dropped() -> None:
    response = httpx.Response(
        400, json={"error": {"message": "WEAK_PASSWORD : Password should be at least 6 characters"}}
    )
    assert module._firebase_code(response) == "WEAK_PASSWORD"
    assert module._firebase_code(httpx.Response(502, text="bad gateway")) == "HTTP_502"


def test_cli_refuses_delete_without_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.mint_load_tokens", "delete"])
    with pytest.raises(SystemExit, match="--yes"):
        module.main()


def test_cli_refuses_to_mint_without_the_web_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["app.ops.mint_load_tokens", "mint", "--state", str(tmp_path / "s.json")],
    )
    with pytest.raises(SystemExit, match="FIREBASE_WEB_API_KEY"):
        module.main()
