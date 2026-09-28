"""The token pool nobody could mint -- capacity blocker ``د-9`` (``docs/
capacity-plan.md`` §0.1, condition (1)) reduced to one command.

**What was missing.** ``deploy/load/`` has been able to run since 0.1 shipped
(``smoke.sh``), and every archived result has carried ``valid: false`` for the
same reason: condition (1) wants REAL Firebase ID tokens, one per user, and
nothing in this repository produced them. ``lib/auth.js`` pointed at a
"minting recipe" in ``README.md`` §2 that was never written, and the recipe,
once written out by hand, is seven steps and a 500-iteration shell loop
(``docs/capacity-wave0-runbook.html``, section ج). This module is that loop,
resumable and quota-aware, plus the three things the loop cannot do: refresh
a pool in seconds, verify it before a thirty-minute run is spent on it, and
delete what it created.

**Why the Web API key and not a service account.** ``auth.js`` used to say
minting "needs a Firebase service account". It does not, and the difference
is the whole reason this tool can exist inside the repository: an ID token
for an Email/Password account is a CLIENT operation -- ``accounts:signUp``
with the project's Web API key, the same key every browser client of the
project already ships. A service account would let this mint *custom*
tokens, and the platform's verifier would refuse those anyway (``iss`` must
be ``https://securetoken.google.com/<project>``, ``firebase_auth.py``). So
the one credential is a public client key, read from ``FIREBASE_WEB_API_KEY``
in the environment -- never a flag, so it never lands in shell history.

**Why every call crosses the real edge.** The tenants behind these tokens are
created by the platform itself: the first authenticated request runs
``provision_on_login`` (``api/middleware/auth.py``) and mints the workspace,
and ``GET /api/v1/me/context`` returns that workspace's id in the same
response. This tool never writes a workspace row, so what the load run
authenticates as is exactly what a real sign-up produces -- which is the point
of condition (1). One consequence is fixed by the platform, not chosen here:
``INV-W1`` gives each user one workspace and no membership route exists, so
**N accounts are N tenants**. 500 tokens (§0's 1,500 sockets ÷ 3 tabs, the
floor ``lib/profile.js`` enforces) are 500 Qdrant collections, above §0's
200-400. ``--count`` does what it is told; the decision is the operator's.

**Resumable, because the quota makes it necessary.** Firebase caps account
creation at **100 accounts per hour per IP address** (its published limit;
the console can schedule a temporary increase). A 500-account run from one
machine therefore stalls at 100 with ``TOO_MANY_ATTEMPTS_TRY_LATER`` unless
the quota was raised first. Sign-ups are serialised through one lane so a
quota hit pauses everything rather than 16 workers each discovering it; the
state file is written after every completed account, so ``mint`` re-run
continues from where it stopped and never creates a duplicate. An account
that signed up but whose tenant was not provisioned (the stack was down) is
finished on the next run, not recreated.

**Two files, two readers.** ``deploy/load/tokens.json`` is what k6 reads --
ID tokens, ``space_id`` per entry, ``"stub": false`` -- and lives one hour.
``deploy/load/accounts.json`` is what THIS tool reads back: the refresh token
and password of every account, so ``refresh`` re-mints the whole pool through
``securetoken.googleapis.com`` in seconds (18,000 exchanges/minute per
project is the limit; 500 is nothing) and ``delete`` can find what it made.
Both are gitignored: the first is credentials, the second is credentials to
credentials. ``include-workspaces.txt`` beside them is the ``--include-
workspace`` list ``app.ops.load_seed run`` needs -- the tenants that must
take the largest shares of the seed, because a corpus whose bulk sits in
workspaces no VU authenticates as is a corpus the harness cannot see.

**Order of operations, and why it is not negotiable.** ``load_seed`` places
``--include-workspace`` tenants at the FIRST ordinals and derives every other
id from the ordinal (``load_seed.py``, ``plan``). So the accounts must exist
before the seed is written, the seed takes tens of minutes, and the tokens
would be dead by the time the run starts -- that is what ``refresh`` is for:
``mint`` → ``load_seed run --include-workspace …`` → ``refresh`` → ``verify``
→ ``deploy/load/run.sh peak``.

``refresh`` does one more thing, and it only makes sense AFTER the seed: the
seed writes its content into spaces of its own (``seed-space-0/1``,
``load_seed._seed_workspace_identity``), never into the ``load`` space
``mint`` created, and four of the five scenarios are scoped to the pool
entry's ``space_id`` (س-32). A pool left pointing at ``load`` sends every
search and listing into an EMPTY space -- measuring the filter, not the
platform, which is what condition (3) exists to forbid. So ``refresh`` lists
each tenant's spaces (``GET /api/v1/spaces`` reports what each one holds)
and points the entry at the fullest, and ``verify`` refuses a pool whose
spaces hold nothing.

The fullest space is also where the seed's heaviest tenants are OVER the
platform's own ceilings -- 13 of 500 seeded spaces above the 1 GiB byte
cap, one tenant above the 10,000-file cap -- so every upload into it
answers ``409``, correctly, and the 2026-09-28 step run spent 59% of its
error budget on exactly that. Reads and uploads therefore point at
different spaces: ``space_id`` stays on the content (what browse and RAG
must measure), and ``upload_space_id`` names a space of the same tenant
with room left, or ``null`` when the tenant has none -- ``index_file.js``
then uploads through another entry instead of measuring the ceiling.

Run from the repository root, like ``load_seed``::

    FIREBASE_WEB_API_KEY=… python -m app.ops.mint_load_tokens mint --count 500
    python -m app.ops.mint_load_tokens refresh
    python -m app.ops.mint_load_tokens verify --duration-s 1800 --ws-vus 1500
    python -m app.ops.mint_load_tokens delete --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jwt

_logger = logging.getLogger(__name__)

# ── Firebase's public client endpoints ────────────────────────────────────
# Identity Toolkit for accounts, Secure Token for refreshes. Both take the Web
# API key as a query parameter; neither takes a bearer of its own.
IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"
SECURE_TOKEN = "https://securetoken.googleapis.com/v1/token"

#: Firebase's documented account-creation limit, per source IP, per hour. The
#: tool cannot raise it; it can only tell the operator where to.
SIGNUP_QUOTA_PER_HOUR = 100
#: How long to wait after a quota refusal before trying again. The window
#: slides, so a minute is the granularity at which capacity comes back.
QUOTA_RETRY_S = 60.0
#: Give up on the quota after roughly one window plus margin, with the state
#: saved: a tool that silently waits five hours is worse than one that says
#: "100 done, raise the quota or re-run in an hour".
DEFAULT_MAX_QUOTA_WAIT_S = 65 * 60.0

#: A Firebase ID token lives an hour (``expiresIn: "3600"``). Not a knob.
ID_TOKEN_LIFETIME_S = 3600
#: ``lib/profile.js``'s guard: more than this many sockets per user and the
#: platform's ``ws_connections_per_user`` ceiling (5) is what gets measured.
MAX_WS_PER_USER = 3.0

#: The platform's per-space byte ceiling and per-tenant file ceiling
#: (``Limits.max_space_bytes`` / ``max_files_per_workspace``), copied rather
#: than imported so this tool stays free of the app's settings; a unit test
#: pins them to ``Limits``. An upload space must sit this far BELOW both, so
#: that the run's own uploads (a few per tenant, ~70 KiB each) cannot fill it
#: mid-run.
SPACE_BYTE_CEILING = 1_073_741_824
WORKSPACE_FILE_CEILING = 10_000
UPLOAD_HEADROOM_BYTES = 64 * 1024 * 1024
UPLOAD_HEADROOM_FILES = 100

#: Transient edge answers: nginx's per-IP ``limit_req`` (429, capacity د-8),
#: the in-flight guard (503, capacity 1.2), an upstream hiccup (502/504).
_RETRYABLE_EDGE = frozenset({429, 502, 503, 504})
_EDGE_RETRIES = 8
_EDGE_BACKOFF_S = 1.5

#: Firebase error messages that mean "the quota", in either spelling.
_QUOTA_MESSAGES = frozenset({"TOO_MANY_ATTEMPTS_TRY_LATER", "QUOTA_EXCEEDED"})
#: Refresh-token failures that a password sign-in can recover from.
_REFRESH_RECOVERABLE = frozenset(
    {"TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN", "USER_NOT_FOUND", "INVALID_GRANT_TYPE"}
)

DEFAULT_STATE = Path("deploy/load/accounts.json")
DEFAULT_POOL = Path("deploy/load/tokens.json")
DEFAULT_INCLUDES = Path("deploy/load/include-workspaces.txt")
DEFAULT_BASE_URL = "https://localhost"
DEFAULT_EMAIL_DOMAIN = "example.com"

SleepFn = Callable[[float], Awaitable[None]]
ProgressFn = Callable[[str], None]


# ── Errors ────────────────────────────────────────────────────────────────


class FirebaseError(RuntimeError):
    """An Identity Toolkit / Secure Token refusal, carrying Firebase's own code."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(f"{code} (HTTP {status})")
        self.code = code
        self.status = status


class QuotaExhausted(RuntimeError):
    """The sign-up quota held for longer than the operator allowed."""


class EdgeError(RuntimeError):
    """The platform answered something the tool cannot continue from."""


# ── State ─────────────────────────────────────────────────────────────────


@dataclass
class Account:
    ordinal: int
    email: str
    password: str
    local_id: str = ""
    refresh_token: str = ""
    id_token: str = ""
    workspace: str | None = None
    space_id: str | None = None
    space_name: str | None = None
    # What ``space_id`` holds, as ``/api/v1/spaces`` last reported it. ``None``
    # is "never looked"; ``0`` is "looked, and empty" -- and ``verify`` treats
    # both as a pool that would measure an empty space.
    space_files: int | None = None
    space_conversations: int | None = None
    # Where ``index_file.js`` uploads: a space of this tenant with room under
    # both ceilings, or ``None`` when it has none (module docstring).
    upload_space_id: str | None = None
    upload_space_bytes: int | None = None

    @property
    def complete(self) -> bool:
        return bool(self.id_token and self.workspace and self.space_id)

    @property
    def holds_content(self) -> bool:
        return bool(self.space_files or self.space_conversations)


@dataclass
class State:
    tag: str
    email_domain: str
    base_url: str
    created_at: str
    accounts: list[Account] = field(default_factory=list)

    @classmethod
    def new(cls, *, base_url: str, email_domain: str, tag: str | None) -> State:
        return cls(
            tag=tag or secrets.token_hex(3),
            email_domain=email_domain,
            base_url=base_url,
            created_at=_now_iso(),
        )

    @classmethod
    def load(cls, path: Path) -> State:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            tag=str(raw["tag"]),
            email_domain=str(raw["email_domain"]),
            base_url=str(raw["base_url"]),
            created_at=str(raw["created_at"]),
            accounts=[Account(**a) for a in raw.get("accounts", [])],
        )

    def save(self, path: Path) -> None:
        # Atomic: a state file torn by Ctrl-C mid-write would lose every
        # refresh token behind it, and those cannot be re-obtained without
        # the password -- which is in the same file.
        payload = {
            "_comment": (
                "Written by python -m app.ops.mint_load_tokens. Refresh tokens and passwords "
                "for the load accounts -- credentials to credentials. Gitignored."
            ),
            "tag": self.tag,
            "email_domain": self.email_domain,
            "base_url": self.base_url,
            "created_at": self.created_at,
            "accounts": [asdict(a) for a in self.accounts],
        }
        _write_atomic(path, json.dumps(payload, indent=2) + "\n")

    def email_for(self, ordinal: int) -> str:
        return f"load-{self.tag}-{ordinal:04d}@{self.email_domain}"

    def by_ordinal(self, ordinal: int) -> Account | None:
        for account in self.accounts:
            if account.ordinal == ordinal:
                return account
        return None


# ── The two HTTP clients, and why they differ on one flag ─────────────────


@dataclass(frozen=True)
class Clients:
    firebase: httpx.AsyncClient
    edge: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.firebase.aclose()
        await self.edge.aclose()


def build_clients(base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> Clients:
    """Firebase VERIFIES TLS: a real key crosses that wire to a real Google
    endpoint. The edge does NOT: it is ``nginx-certs``' self-signed certificate
    (capacity ح-19), the same ``-k`` the healthcheck and the k6 harness use
    (``lib/config.js``, ``TLS_GLOBAL_OPTIONS``). Skipping verification there
    asserts nothing about the certificate; only that this tool is not the one
    measuring it."""
    timeout = httpx.Timeout(30.0)
    firebase = httpx.AsyncClient(timeout=timeout, transport=transport)
    edge = httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout, verify=False, transport=transport
    )
    return Clients(firebase=firebase, edge=edge)


# ── Firebase calls ────────────────────────────────────────────────────────


def _firebase_code(response: httpx.Response) -> str:
    """Identity Toolkit puts its reason in ``error.message``, sometimes with a
    ``" : human text"`` suffix (``WEAK_PASSWORD : Password should be…``)."""
    try:
        message = str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP_{response.status_code}"
    return message.split(":", 1)[0].strip() or f"HTTP_{response.status_code}"


async def _firebase_post(
    client: httpx.AsyncClient, url: str, api_key: str, **request: Any
) -> dict[str, Any]:
    response = await client.post(url, params={"key": api_key}, **request)
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        raise FirebaseError("TOO_MANY_ATTEMPTS_TRY_LATER", response.status_code)
    if response.is_error:
        raise FirebaseError(_firebase_code(response), response.status_code)
    body: dict[str, Any] = response.json()
    return body


async def sign_up(client: httpx.AsyncClient, api_key: str, email: str, password: str) -> Account:
    body = await _firebase_post(
        client,
        f"{IDENTITY_TOOLKIT}/accounts:signUp",
        api_key,
        json={"email": email, "password": password, "returnSecureToken": True},
    )
    return Account(
        ordinal=-1,
        email=email,
        password=password,
        local_id=str(body["localId"]),
        refresh_token=str(body["refreshToken"]),
        id_token=str(body["idToken"]),
    )


async def sign_in(client: httpx.AsyncClient, api_key: str, account: Account) -> None:
    body = await _firebase_post(
        client,
        f"{IDENTITY_TOOLKIT}/accounts:signInWithPassword",
        api_key,
        json={"email": account.email, "password": account.password, "returnSecureToken": True},
    )
    account.id_token = str(body["idToken"])
    account.refresh_token = str(body["refreshToken"])
    account.local_id = str(body.get("localId") or account.local_id)


async def exchange_refresh_token(client: httpx.AsyncClient, api_key: str, account: Account) -> None:
    # Form-encoded, per the Secure Token API; JSON is accepted too but the
    # documented shape is the one to send.
    body = await _firebase_post(
        client,
        SECURE_TOKEN,
        api_key,
        data={"grant_type": "refresh_token", "refresh_token": account.refresh_token},
    )
    account.id_token = str(body["id_token"])
    account.refresh_token = str(body.get("refresh_token") or account.refresh_token)


async def renew(client: httpx.AsyncClient, api_key: str, account: Account) -> str:
    """A fresh ID token by the cheap path, falling back to the password when
    the refresh token is no longer honoured. Returns which path worked."""
    try:
        await exchange_refresh_token(client, api_key, account)
    except FirebaseError as exc:
        if exc.code not in _REFRESH_RECOVERABLE:
            raise
        await sign_in(client, api_key, account)
        return "password"
    return "refresh"


async def delete_account(client: httpx.AsyncClient, api_key: str, account: Account) -> None:
    await _firebase_post(
        client, f"{IDENTITY_TOOLKIT}/accounts:delete", api_key, json={"idToken": account.id_token}
    )


# ── Platform calls, through the edge ──────────────────────────────────────


async def _edge_request(
    clients: Clients,
    method: str,
    path: str,
    account: Account,
    *,
    sleep: SleepFn,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One authenticated request, retried through the edge's own refusals.

    This tool runs from ONE address and does not claim thirty-two (that is
    the generator's trick, ``entrypoint.sh``, and it needs ``NET_ADMIN``); at
    nginx's 20 r/s per address, a thousand provisioning calls are under a
    minute, and a 429 is a pause, not a failure.
    """
    headers = {"Authorization": f"Bearer {account.id_token}"}
    last_status = 0
    for attempt in range(_EDGE_RETRIES):
        response = await clients.edge.request(
            method, path, headers=headers, json=json_body, params=params
        )
        last_status = response.status_code
        if response.status_code in _RETRYABLE_EDGE:
            await sleep(_EDGE_BACKOFF_S * (attempt + 1))
            continue
        if response.is_error:
            raise EdgeError(
                f"{method} {path} for {account.email}: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )
        body: dict[str, Any] = response.json()
        return body
    raise EdgeError(
        f"{method} {path} for {account.email}: still {last_status} after {_EDGE_RETRIES} "
        "attempts -- is the stack up, and is this the edge?"
    )


async def provision(clients: Clients, account: Account, *, sleep: SleepFn) -> None:
    """``/me/context`` is the request that CREATES the tenant (JIT) and the
    one that reports its id, in the same round trip."""
    body = await _edge_request(clients, "GET", "/api/v1/me/context", account, sleep=sleep)
    account.workspace = str(body["workspace"]["id"])


async def create_space(clients: Clients, account: Account, *, sleep: SleepFn) -> None:
    """Every scenario but one needs a ``space_id`` (س-32); ``auth.js`` refuses
    a pool entry without it."""
    body = await _edge_request(
        clients, "POST", "/api/v1/spaces", account, sleep=sleep, json_body={"name": "load"}
    )
    account.space_id = str(body["id"])
    account.space_name = str(body.get("name") or "load")
    account.space_files = 0
    account.space_conversations = 0
    account.upload_space_id = account.space_id
    account.upload_space_bytes = 0


async def point_at_content(clients: Clients, account: Account, *, sleep: SleepFn) -> bool:
    """Move the entry to the space that HOLDS something, if one exists.

    The seed never writes into the space ``create_space`` made: it writes into
    two of its own per workspace (``seed-space-0/1``), even for an
    ``--include-workspace`` tenant. Four of the five scenarios are scoped to
    the entry's ``space_id``, so an entry that stayed on ``load`` would search
    and list an empty space -- the measurement condition (3) forbids.
    ``/api/v1/spaces`` reports ``file_count`` and ``conversation_count`` per
    space; the fullest wins, ``load`` stays only while nothing else holds
    anything. Returns whether the entry now points at content.
    """
    spaces: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"limit": "100"} | ({"cursor": cursor} if cursor else {})
        page = await _edge_request(
            clients, "GET", "/api/v1/spaces", account, sleep=sleep, params=params
        )
        spaces.extend(page.get("data") or [])
        cursor = (page.get("meta") or {}).get("next_cursor") or None
        if cursor is None:
            break

    def _held(space: dict[str, Any]) -> tuple[int, int]:
        return int(space.get("file_count") or 0), int(space.get("conversation_count") or 0)

    point_uploads(account, spaces)
    fullest = max(spaces, key=_held, default=None)
    if fullest is None or _held(fullest) == (0, 0):
        account.space_files, account.space_conversations = 0, 0
        return False
    account.space_id = str(fullest["id"])
    account.space_name = str(fullest.get("name") or "")
    account.space_files, account.space_conversations = _held(fullest)
    return True


def point_uploads(account: Account, spaces: list[dict[str, Any]]) -> None:
    """Name the space ``index_file.js`` uploads into: the fullest one still
    under both ceilings with headroom, so an upload lands among content when
    it can, and ``None`` when the tenant has no such space.

    The file ceiling is the TENANT's, so it is summed over every listed
    space: a tenant at 10,000 files has no room in its emptiest space either.
    """
    files = sum(int(s.get("file_count") or 0) for s in spaces)
    if files + UPLOAD_HEADROOM_FILES > WORKSPACE_FILE_CEILING:
        account.upload_space_id, account.upload_space_bytes = None, None
        return
    roomy = [
        s
        for s in spaces
        if int(s.get("bytes_used") or 0) + UPLOAD_HEADROOM_BYTES <= SPACE_BYTE_CEILING
    ]
    best = max(roomy, key=lambda s: int(s.get("file_count") or 0), default=None)
    if best is None:
        account.upload_space_id, account.upload_space_bytes = None, None
        return
    account.upload_space_id = str(best["id"])
    account.upload_space_bytes = int(best.get("bytes_used") or 0)


# ── mint ──────────────────────────────────────────────────────────────────


class _SignUpLane:
    """Sign-ups go one at a time, so the quota is met by ONE worker that waits
    while the others queue behind it -- instead of sixteen workers each
    hitting it, each backing off, and the operator reading sixteen warnings."""

    def __init__(self, *, sleep: SleepFn, max_wait_s: float, progress: ProgressFn) -> None:
        self._lock = asyncio.Lock()
        self._sleep = sleep
        self._max_wait_s = max_wait_s
        self._waited_s = 0.0
        self._progress = progress
        self._announced = False

    async def run(
        self, client: httpx.AsyncClient, api_key: str, email: str, password: str, done: int
    ) -> Account:
        async with self._lock:
            while True:
                try:
                    return await sign_up(client, api_key, email, password)
                except FirebaseError as exc:
                    if exc.code not in _QUOTA_MESSAGES:
                        raise
                    await self._hold(done)

    async def _hold(self, done: int) -> None:
        if not self._announced:
            self._announced = True
            self._progress(
                f"Firebase's sign-up quota: {SIGNUP_QUOTA_PER_HOUR} accounts/hour per IP "
                f"({done} created so far). Either schedule a temporary increase in the "
                "console (Authentication → Settings → Sign-up quota) or leave this running: "
                f"it retries every {QUOTA_RETRY_S:.0f}s and every finished account is saved."
            )
        if self._waited_s >= self._max_wait_s:
            raise QuotaExhausted(
                f"waited {self._waited_s / 60:.0f} min on the sign-up quota with {done} "
                "accounts created; state is saved -- raise the quota, or re-run later to resume."
            )
        self._waited_s += QUOTA_RETRY_S
        await self._sleep(QUOTA_RETRY_S)


@dataclass
class MintReport:
    created: int = 0
    provisioned: int = 0
    failed: list[str] = field(default_factory=list)


async def mint(
    state: State,
    *,
    count: int,
    api_key: str,
    clients: Clients,
    state_path: Path,
    concurrency: int = 8,
    max_quota_wait_s: float = DEFAULT_MAX_QUOTA_WAIT_S,
    sleep: SleepFn = asyncio.sleep,
    progress: ProgressFn = _logger.info,
) -> MintReport:
    """Bring ``state`` to ``count`` complete accounts, doing only what is
    missing for each ordinal: sign-up, then tenant, then space."""
    report = MintReport()
    lane = _SignUpLane(sleep=sleep, max_wait_s=max_quota_wait_s, progress=progress)
    gate = asyncio.Semaphore(concurrency)
    save_lock = asyncio.Lock()

    async def _save() -> None:
        async with save_lock:
            state.save(state_path)

    async def _one(ordinal: int) -> None:
        async with gate:
            account = state.by_ordinal(ordinal)
            try:
                if account is None:
                    created = await lane.run(
                        clients.firebase,
                        api_key,
                        state.email_for(ordinal),
                        secrets.token_urlsafe(18),
                        len(state.accounts),
                    )
                    created.ordinal = ordinal
                    state.accounts.append(created)
                    state.accounts.sort(key=lambda a: a.ordinal)
                    account = created
                    report.created += 1
                    await _save()
                if account.workspace is None:
                    await provision(clients, account, sleep=sleep)
                if account.space_id is None:
                    await create_space(clients, account, sleep=sleep)
                    report.provisioned += 1
                    await _save()
            except (FirebaseError, EdgeError) as exc:
                report.failed.append(f"{state.email_for(ordinal)}: {exc}")

    pending = [o for o in range(count) if not _is_complete(state.by_ordinal(o))]
    progress(f"{count - len(pending)} of {count} accounts already complete; {len(pending)} to do")
    try:
        await asyncio.gather(*(_one(o) for o in pending))
    finally:
        state.save(state_path)
    return report


def _is_complete(account: Account | None) -> bool:
    return account is not None and account.complete


# ── refresh / delete ──────────────────────────────────────────────────────


@dataclass
class RenewReport:
    by_refresh: int = 0
    by_password: int = 0
    on_content: int = 0
    on_empty: int = 0
    no_upload_room: int = 0
    failed: list[str] = field(default_factory=list)


async def refresh(
    state: State,
    *,
    api_key: str,
    clients: Clients,
    concurrency: int = 16,
    sleep: SleepFn = asyncio.sleep,
) -> RenewReport:
    """A new ID token for every account -- and, because this is the step
    that runs after the seed, each entry moved to the space the seed filled
    (``point_at_content``)."""
    report = RenewReport()
    gate = asyncio.Semaphore(concurrency)

    async def _one(account: Account) -> None:
        async with gate:
            try:
                path = await renew(clients.firebase, api_key, account)
            except FirebaseError as exc:
                report.failed.append(f"{account.email}: {exc}")
                return
            if path == "refresh":
                report.by_refresh += 1
            else:
                report.by_password += 1
            try:
                pointed = await point_at_content(clients, account, sleep=sleep)
            except EdgeError as exc:
                report.failed.append(f"{account.email}: {exc}")
                return
            if pointed:
                report.on_content += 1
            else:
                report.on_empty += 1
            if account.upload_space_id is None:
                report.no_upload_room += 1

    await asyncio.gather(*(_one(a) for a in state.accounts))
    return report


@dataclass
class DeleteReport:
    deleted: int = 0
    failed: list[str] = field(default_factory=list)


async def delete_all(
    state: State,
    *,
    api_key: str,
    clients: Clients,
    concurrency: int = 16,
) -> DeleteReport:
    """``accounts:delete`` wants a CURRENT ID token, so each deletion is a
    renewal first. Deleted accounts leave the state as they go."""
    report = DeleteReport()
    gate = asyncio.Semaphore(concurrency)
    survivors: list[Account] = []

    async def _one(account: Account) -> None:
        async with gate:
            try:
                await renew(clients.firebase, api_key, account)
                await delete_account(clients.firebase, api_key, account)
            except FirebaseError as exc:
                if exc.code == "USER_NOT_FOUND":
                    report.deleted += 1  # already gone: the outcome asked for
                    return
                report.failed.append(f"{account.email}: {exc}")
                survivors.append(account)
                return
            report.deleted += 1

    await asyncio.gather(*(_one(a) for a in state.accounts))
    state.accounts = sorted(survivors, key=lambda a: a.ordinal)
    return report


# ── The pool file, and its verification ───────────────────────────────────


def write_pool(state: State, path: Path) -> int:
    """``tokens.json`` in exactly the shape ``lib/auth.js`` reads: ``tokens[]``
    of ``workspace`` / ``space_id`` / ``id_token``, and ``stub`` declared
    false -- a declaration, not a switch, and the one that lets the archived
    result read ``valid: true``."""
    complete = [a for a in state.accounts if a.complete]
    payload = {
        "_comment": (
            "Minted by python -m app.ops.mint_load_tokens; ID tokens live one hour -- "
            "`refresh` rewrites this file from deploy/load/accounts.json. Gitignored."
        ),
        "stub": False,
        "minted_at": _now_iso(),
        "minted_by": "app.ops.mint_load_tokens",
        "tag": state.tag,
        "tokens": [
            {
                "workspace": a.workspace,
                "space_id": a.space_id,
                "id_token": a.id_token,
                # Read by `verify` and by people; `lib/auth.js` ignores them.
                "space_name": a.space_name,
                "space_files": a.space_files,
                "space_conversations": a.space_conversations,
                # Read by `index_file.js`: `null` means this tenant is at a
                # ceiling, and its uploads go through another entry.
                "upload_space_id": a.upload_space_id,
                "upload_space_bytes": a.upload_space_bytes,
            }
            for a in complete
        ],
    }
    _write_atomic(path, json.dumps(payload, indent=2) + "\n")
    return len(complete)


def write_includes(state: State, path: Path) -> int:
    ids = [a.workspace for a in state.accounts if a.complete and a.workspace]
    _write_atomic(path, "".join(f"{w}\n" for w in ids))
    return len(ids)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def token_claims(id_token: str) -> dict[str, Any]:
    """The payload, UNVERIFIED -- the platform verifies; this only reads
    ``exp``/``aud``/``iss`` to predict what the platform will say."""
    try:
        claims: dict[str, Any] = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}
    return claims


def verify_pool(
    pool: dict[str, Any],
    *,
    now: int,
    duration_s: int,
    ws_vus: int,
    project_id: str | None,
) -> list[Check]:
    """The checks ``lib/profile.js`` makes in ``setup()`` -- before the run
    exists, so a refusal costs a second instead of the seed and the wait."""
    tokens: list[dict[str, Any]] = list(pool.get("tokens") or [])
    n = len(tokens)
    checks: list[Check] = []

    minimum = -(-ws_vus // int(MAX_WS_PER_USER))  # ceil
    checks.append(Check("tokens", n >= minimum, f"{n} (need ≥ {minimum} for {ws_vus} WS VUs)"))
    checks.append(Check("stub", pool.get("stub") is False, repr(pool.get("stub"))))

    incomplete = sum(1 for t in tokens if not (t.get("workspace") and t.get("space_id")))
    checks.append(Check("workspace+space_id", incomplete == 0, f"{incomplete} entries missing one"))

    distinct = len({t.get("workspace") for t in tokens})
    checks.append(Check("distinct tenants", distinct == n, f"{distinct} of {n}"))

    per_user = ws_vus / n if n else float("inf")
    checks.append(Check("ws per user", per_user <= MAX_WS_PER_USER, f"{per_user:.2f} (≤ 3.00)"))

    # Condition (3), seen from the pool's side: the seed fills spaces of its
    # own, and only `refresh` (after the seed) moves an entry onto one.
    held = sum(
        1 for t in tokens if (t.get("space_files") or 0) + (t.get("space_conversations") or 0)
    )
    checks.append(
        Check(
            "space content",
            n > 0 and held == n,
            f"{held} of {n} entries point at a space holding files or conversations"
            + ("" if held == n else " -- seed first, then `refresh` (README §2)"),
        )
    )

    # And the upload side (د-33): a pool from before `upload_space_id` has no
    # answer at all, which `index_file.js` would read as "upload into the
    # content space" -- the 409s this field exists to avoid. Tenants at a
    # ceiling are expected (`null`) and only reported; a pool where NOBODY
    # can upload cannot run the index scenario.
    unanswered = sum(1 for t in tokens if "upload_space_id" not in t)
    uploaders = sum(1 for t in tokens if t.get("upload_space_id"))
    checks.append(
        Check(
            "upload room",
            unanswered == 0 and uploaders > 0,
            (
                f"{unanswered} entries have no upload_space_id -- run `refresh`"
                if unanswered
                else f"{uploaders} of {n} entries can upload; {n - uploaders} are at a ceiling"
            ),
        )
    )

    claims = [token_claims(str(t.get("id_token", ""))) for t in tokens]
    exps = [int(c["exp"]) for c in claims if c.get("exp")]
    remaining = (min(exps) - now) if exps else -1
    checks.append(
        Check(
            "earliest exp",
            remaining >= duration_s,
            f"in {remaining}s (run is {duration_s}s)" if exps else "no exp claim readable",
        )
    )

    auds = {str(c.get("aud")) for c in claims if c}
    if project_id:
        checks.append(Check("aud == FIREBASE_PROJECT_ID", auds == {project_id}, f"{sorted(auds)}"))
        expected_iss = f"https://securetoken.google.com/{project_id}"
        isss = {str(c.get("iss")) for c in claims if c}
        checks.append(Check("iss", isss == {expected_iss}, f"{sorted(isss)}"))
    else:
        checks.append(Check("aud", True, f"{sorted(auds)} (set FIREBASE_PROJECT_ID to assert it)"))
    return checks


# ── CLI ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _api_key() -> str:
    key = os.environ.get("FIREBASE_WEB_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "FIREBASE_WEB_API_KEY is not set. It is the project's Web API key "
            "(Firebase console → Project settings → General → Web API key): a client key, "
            "not a service account, and an environment variable, not a flag."
        )
    return key


def _load_state(path: Path) -> State:
    if not path.exists():
        raise SystemExit(f"{path} does not exist -- nothing was minted on this machine yet.")
    return State.load(path)


def _print(line: str) -> None:
    print(line, file=sys.stderr)


def _emit_pool(state: State, args: argparse.Namespace) -> None:
    written = write_pool(state, args.out)
    included = write_includes(state, args.includes)
    complete = [a for a in state.accounts if a.complete]
    exps = [token_claims(a.id_token).get("exp") for a in complete]
    earliest = min((int(e) for e in exps if e), default=None)
    _print(f"pool      : {args.out}  ({written} tokens, stub=false)")
    _print(f"includes  : {args.includes}  ({included} workspace ids)")
    if earliest is not None:
        _print(f"earliest  : expires in {earliest - int(time.time())}s")
    _print(
        "seed with : python -m app.ops.load_seed run --seed-id <id> "
        f"$(sed 's/^/--include-workspace /' {args.includes})"
    )


async def _act_mint(args: argparse.Namespace) -> int:
    api_key = _api_key()
    state = (
        State.load(args.state)
        if args.state.exists()
        else State.new(base_url=args.base_url, email_domain=args.email_domain, tag=args.tag)
    )
    if args.state.exists() and args.tag and args.tag != state.tag:
        raise SystemExit(
            f"{args.state} already holds tag '{state.tag}'; a second tag means a second state "
            "file (--state). Accounts are named by tag, and two tags in one file would be two "
            "pools pretending to be one."
        )
    clients = build_clients(state.base_url)
    try:
        report = await mint(
            state,
            count=args.count,
            api_key=api_key,
            clients=clients,
            state_path=args.state,
            concurrency=args.concurrency,
            max_quota_wait_s=args.max_quota_wait_s,
            progress=_print,
        )
    except QuotaExhausted as exc:
        _print(f"stopped   : {exc}")
        return 1
    finally:
        await clients.aclose()
    _print(f"created   : {report.created} accounts, {report.provisioned} tenants provisioned")
    for line in report.failed:
        _print(f"failed    : {line}")
    if any("EMAIL_EXISTS" in line for line in report.failed):
        _print(
            "            EMAIL_EXISTS: an earlier mint left accounts with this tag in the "
            "project and its accounts.json is gone. Pass --tag <new> to mint beside them, "
            "or delete them in the console."
        )
    _emit_pool(state, args)
    return 1 if report.failed else 0


async def _act_refresh(args: argparse.Namespace) -> int:
    api_key = _api_key()
    state = _load_state(args.state)
    clients = build_clients(state.base_url)
    try:
        report = await refresh(state, api_key=api_key, clients=clients)
    finally:
        await clients.aclose()
    state.save(args.state)
    _print(f"renewed   : {report.by_refresh} by refresh token, {report.by_password} by password")
    _print(
        f"spaces    : {report.on_content} entries point at seeded content, "
        f"{report.on_empty} at an empty space"
    )
    if report.on_empty:
        _print(
            "            an empty space measures the filter, not the platform (condition 3): "
            "run the seed with include-workspaces.txt, then `refresh` again."
        )
    _print(
        f"uploads   : {len(state.accounts) - report.no_upload_room} entries upload into a space "
        f"with room, {report.no_upload_room} tenants are at a ceiling and do not upload"
    )
    for line in report.failed:
        _print(f"failed    : {line}")
    _emit_pool(state, args)
    return 1 if report.failed else 0


def _act_verify(args: argparse.Namespace) -> int:
    if not args.out.exists():
        raise SystemExit(f"{args.out} does not exist; run `mint` first.")
    pool = json.loads(args.out.read_text(encoding="utf-8"))
    checks = verify_pool(
        pool,
        now=int(time.time()),
        duration_s=args.duration_s,
        ws_vus=args.ws_vus,
        project_id=os.environ.get("FIREBASE_PROJECT_ID", "").strip() or None,
    )
    width = max(len(c.name) for c in checks)
    for check in checks:
        _print(f"{'ok ' if check.ok else 'NO '} {check.name.ljust(width)}  {check.detail}")
    return 0 if all(c.ok for c in checks) else 1


async def _act_delete(args: argparse.Namespace) -> int:
    api_key = _api_key()
    state = _load_state(args.state)
    clients = build_clients(state.base_url)
    try:
        report = await delete_all(state, api_key=api_key, clients=clients)
    finally:
        await clients.aclose()
    _print(f"deleted   : {report.deleted} Firebase accounts")
    for line in report.failed:
        _print(f"failed    : {line}")
    if state.accounts:
        state.save(args.state)
        return 1
    for path in (args.state, args.out, args.includes):
        if path.exists():
            path.unlink()
    _print(
        "            The tenants those accounts created remain in Postgres and Qdrant -- "
        "`python -m app.ops.purge` is the tool for those."
    )
    return 0


def _add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="accounts file")
    parser.add_argument("--out", type=Path, default=DEFAULT_POOL, help="the k6 token pool")
    parser.add_argument(
        "--includes", type=Path, default=DEFAULT_INCLUDES, help="workspace ids, one per line"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.mint_load_tokens",
        description="Mint the real Firebase token pool capacity step 0.1 condition (1) requires "
        "(module docstring). Reads FIREBASE_WEB_API_KEY from the environment.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    mint_parser = sub.add_parser(
        "mint", help="create accounts and tenants up to --count (resumable)"
    )
    mint_parser.add_argument("--count", type=int, default=500, help="accounts = tenants")
    mint_parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="the EDGE, through TLS (condition 2); recorded in the state file",
    )
    mint_parser.add_argument(
        "--tag", default=None, help="names the accounts (load-<tag>-0001@…); random by default"
    )
    mint_parser.add_argument("--email-domain", default=DEFAULT_EMAIL_DOMAIN)
    mint_parser.add_argument("--concurrency", type=int, default=8)
    mint_parser.add_argument(
        "--max-quota-wait-s",
        type=float,
        default=DEFAULT_MAX_QUOTA_WAIT_S,
        help=f"how long to keep retrying the {SIGNUP_QUOTA_PER_HOUR}/hour sign-up quota",
    )
    _add_paths(mint_parser)

    refresh_parser = sub.add_parser(
        "refresh",
        help="new ID tokens for every account, in seconds -- and each entry moved onto the "
        "space the seed filled (run it AFTER the seed)",
    )
    _add_paths(refresh_parser)

    verify_parser = sub.add_parser("verify", help="lib/profile.js's guards, before the run")
    verify_parser.add_argument("--duration-s", type=int, default=1800, help="peak is 1800")
    verify_parser.add_argument("--ws-vus", type=int, default=1500, help="peak holds 1500")
    _add_paths(verify_parser)

    delete_parser = sub.add_parser("delete", help="delete every account this tool created")
    delete_parser.add_argument("--yes", action="store_true", help="required")
    _add_paths(delete_parser)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "delete" and not args.yes:
        raise SystemExit(
            "delete refused: pass --yes. This deletes every Firebase account in the state "
            "file; the tenants they created stay in Postgres and Qdrant (app.ops.purge)."
        )
    if args.action == "verify":
        raise SystemExit(_act_verify(args))
    actions = {"mint": _act_mint, "refresh": _act_refresh, "delete": _act_delete}
    raise SystemExit(asyncio.run(actions[args.action](args)))


if __name__ == "__main__":
    main()
