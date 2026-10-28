"""In-container smoke checks for the 7.1 topology.

Run inside the `app` service, against the real wiring:

    docker compose exec -T app python /app/deploy/smoke/stack_smoke.py

`/health/ready` deliberately touches no dependency (health.py: readiness is
"startup done", not "dependencies reachable"), so a healthy container is NOT
by itself evidence that the data path works. These checks supply that
evidence.

  1. **OPS-02 through the containerised PgBouncer.** Transaction pooling
     requires asyncpg's statement_cache_size=0 and no named prepared
     statements. `create_engine` sets it for every engine; this executes real
     statements over the pooler to confirm it holds there too -- the failure
     mode is `DuplicatePreparedStatementError` on the SECOND transaction
     reusing a backend, so one query would not catch it.
  2. **RLS is live and fails closed.** A tenant table read with no
     `app.workspace_id` set must return zero rows, not an error and not
     somebody's data.
  3. **Presigned URLs name the PUBLIC address** (7.1). The whole point of
     MINIO_PUBLIC_ENDPOINT: the URL handed to a browser must not say
     `minio:9000`.
  4. **A presigned PUT and GET through the public address round-trip the
     bytes.** [3] reads the URL and never sends it, and that is not enough: a
     proxy that rewrites the Host SigV4 signed passes [3] while every browser
     upload fails `403 SignatureDoesNotMatch`. Only a real round trip proves
     it, and it runs only where the public address is reachable from inside
     this container:
       * on a Compose server, only when the operator asks for it after the
         name has a DNS record and the origin certificate is in place
         (docs/deploy-server.md §5.3):
             docker compose exec -T -e SMOKE_PUBLIC_ROUNDTRIP=1 app \
                 python /app/deploy/smoke/stack_smoke.py
         install.sh never sets it: the first install precedes both, and a
         failure here would end it with exit 71;
       * on the dev box it is skipped: the URLs name the operator's
         localhost:19000, which from in here is this container itself.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Mapping
from typing import Protocol

import httpx
from sqlalchemy import text

from app.framework.errors import NotFoundError
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings
from app.infrastructure.config.vault_auth import load_vault_auth
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.secrets.vault_secrets import VaultSecrets, create_vault_client
from app.infrastructure.storage.minio_storage import (
    MinioStorage,
    create_minio_client,
    create_minio_signing_client,
)

_INTERNAL_HOST = "minio:9000"
_ROUNDTRIP_ENV = "SMOKE_PUBLIC_ROUNDTRIP"
# The line install.sh's output carries when [4] is skipped
# (tests/unit/test_stack_smoke_roundtrip.py pins it).
SKIPPED_LINE = "[4] presigned PUT round trip: skipped (SMOKE_PUBLIC_ROUNDTRIP unset)"
_PROBE_KEY = "smoke/presign-put-probe.txt"
_PROBE_BODY = b"stack_smoke probe\n"
_PROBE_TTL_S = 300
_S3_CODE = re.compile(r"<Code>([A-Za-z]+)</Code>")
_SIGNATURE_PARAM = re.compile(r"X-Amz-[A-Za-z-]+=[^&\s'\"]+")


async def check_pgbouncer() -> None:
    db = load_settings().database
    print(f"[1] database url host: {db.url.split('@')[-1]}")
    # ⚠️ The deployed DSN, but NOT the deployed request-path budget (capacity
    # step 2.6). This script is an operator tool invoked by hand, the
    # `app.ops.*` footing -- and it was measured being broken by the other
    # reading: with the API's own 5s `statement_timeout` inherited, check [2]
    # below failed with `57014` on a COLD buffer cache and passed on a warm
    # one. A proof whose verdict depends on what Postgres happens to have in
    # memory is not a proof, and the number it was failing against was chosen
    # for HTTP requests, which this is not.
    engine = create_engine(DatabaseSettings(url=db.url))
    try:
        # Several separate transactions: under transaction pooling each may
        # land on a different backend, and a re-prepared named statement is
        # exactly what OPS-02 exists to prevent.
        for i in range(5):
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT count(*) FROM files.files"))
                count = result.scalar_one()
            if i == 0:
                print(f"    files.files visible rows (no RLS context): {count}")
        print("[1] OPS-02: 5 transactions through pgbouncer, no prepared-statement clash  OK")

        # RLS fails closed: no app.workspace_id => zero rows, not an error.
        #
        # ⚠️ `LIMIT 1` inside, and that is the claim itself rather than a
        # weakening of it: "no row is visible" is exactly what fail-closed
        # means, and it is what a bare `count(*)` was spending a full scan of
        # `conversations.messages` to establish. Since the load-test corpus
        # (`0.1`) that table holds 1,000,024 rows, and the aggregate was
        # measured at 1.58s warm on a plan whose cost is 101,558 -- for an
        # answer that is always zero. The bounded probe takes 0.15s and cannot
        # drift with the corpus.
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT count(*) FROM (SELECT 1 FROM conversations.messages LIMIT 1) p")
                )
            ).scalar_one()
        if rows != 0:
            raise SystemExit(
                "FAIL: RLS did not fail closed -- a row is visible without a workspace context"
            )
        print("[2] RLS fails closed without app.workspace_id (0 rows)  OK")
    finally:
        await engine.dispose()


async def check_presign() -> None:
    settings = load_settings()
    minio = settings.minio
    print(f"[3] endpoint={minio.endpoint}  public={minio.public_endpoint or '(unset)'}")

    # Signs with the same material the composition root reads at startup.
    # The same auth resolution it performs, too -- never a bare
    # `token=None`, which suppresses hvac's own env fallback and yields a 403.
    auth = load_vault_auth()
    client_v = create_vault_client(settings.vault, token=auth.token, secret_id=auth.secret_id)
    secrets = VaultSecrets(client_v)
    material = await secrets.get_secret("secret/data/minio")

    client = create_minio_client(
        minio, access_key=material["access_key"], secret_key=material["secret_key"]
    )
    signing = create_minio_signing_client(
        minio, client, access_key=material["access_key"], secret_key=material["secret_key"]
    )
    storage = MinioStorage(client, minio.bucket, signing)

    url = await storage.presign_get("smoke/probe.txt", 300)
    shown = url.split("?")[0]
    print(f"    presigned GET -> {shown}")
    if _INTERNAL_HOST in url:
        raise SystemExit(
            f"FAIL: presigned URL names the INTERNAL host {_INTERNAL_HOST} -- "
            "no browser can resolve it (MINIO_PUBLIC_ENDPOINT not in effect)"
        )
    if minio.public_endpoint and minio.public_endpoint not in url:
        raise SystemExit(f"FAIL: presigned URL does not name {minio.public_endpoint}")
    print("[3] presigned URL names the public address, not minio:9000  OK")

    await check_public_roundtrip(os.environ, storage, lambda: httpx.AsyncClient(timeout=30))


class ProbeStorage(Protocol):
    """What [4] needs from MinioStorage (structural match, nothing new in src/)."""

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str: ...

    async def presign_get(self, key: str, ttl_s: int) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


def roundtrip_reason(environ: Mapping[str, str]) -> str | None:
    """Why [4] runs here, or None to skip it.

    "SMOKE_PUBLIC_ROUNDTRIP=1" when the operator asked for it on a Compose server;
    None otherwise -- the dev box, and every install.sh run.
    Raises SystemExit, naming the variable, when it holds anything but "" or "1":
    an operator who typed `=true` must not read "skipped" as "passed".
    """
    opt_in = environ.get(_ROUNDTRIP_ENV, "")
    if opt_in not in ("", "1"):
        raise SystemExit(f"FAIL: {_ROUNDTRIP_ENV} must be 1 or unset, got {opt_in!r}")
    if opt_in == "1":
        return f"{_ROUNDTRIP_ENV}=1"
    return None


def _shown(url: str) -> str:
    """A URL up to its '?': the query carries the signature."""
    return url.split("?", 1)[0]


def _scrub(text: str) -> str:
    return _SIGNATURE_PARAM.sub("X-Amz-...=<redacted>", text)


_CLOUDFLARE_ORIGIN_ERRORS = range(520, 531)
_STATUS_HINTS: dict[int, str] = {
    int(httpx.codes.REQUEST_ENTITY_TOO_LARGE): (
        "over the edge's client_max_body_size or Cloudflare's 100 MB"
    ),
    int(
        httpx.codes.NOT_FOUND
    ): "this edge does not route /workspace-files/ (a release without 6.2)",
    int(httpx.codes.FORBIDDEN): "refused by the edge (GET, HEAD and PUT only)",
    int(httpx.codes.BAD_GATEWAY): "the edge could not reach MinIO",
}


def failure_hint(response: httpx.Response) -> str:
    """One short reason for a non-200, in this order: the S3 `<Code>` from MinIO's
    XML body; a Cloudflare challenge; then the status class."""
    code = _S3_CODE.search(response.text)
    if code:
        return code.group(1)
    if "cf-mitigated" in response.headers:
        return "Cloudflare challenged this client (cf-mitigated)"
    if response.status_code in _CLOUDFLARE_ORIGIN_ERRORS:
        return "Cloudflare could not use the origin (docs/deploy-server.md §4.8)"
    return _STATUS_HINTS.get(response.status_code, "unexpected answer")


async def public_roundtrip(storage: ProbeStorage, http: httpx.AsyncClient) -> None:
    """PUT the probe through the public URL, read it back inside, GET it through
    the public URL and compare. The probe object is deleted in every case.
    Raises SystemExit on any failure. No printed line and no message carries a
    query string: URLs are shown up to '?'."""
    put_url = await storage.presign_put(_PROBE_KEY, _PROBE_TTL_S, "text/plain")
    base = _shown(put_url)
    print(f"    PUT -> {base}")
    try:
        try:
            response = await http.put(
                put_url, content=_PROBE_BODY, headers={"Content-Type": "text/plain"}
            )
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"FAIL: presigned PUT through {base} got no answer: "
                f"{type(exc).__name__}: {_scrub(str(exc))} -- browser uploads are broken"
            ) from None
        if response.status_code != httpx.codes.OK:
            raise SystemExit(
                f"FAIL: presigned PUT through {base} answered {response.status_code} "
                f"({failure_hint(response)}) -- browser uploads are broken"
            )
        try:
            stored: bytes | None = await storage.get(_PROBE_KEY)
        except NotFoundError:  # MinioStorage.get raises for a missing key
            stored = None
        if stored != _PROBE_BODY:
            raise SystemExit("FAIL: presigned PUT answered 200 but the object is not in MinIO")
        print("[4] presigned PUT through the public address stored the bytes  OK")

        get_url = await storage.presign_get(_PROBE_KEY, _PROBE_TTL_S)
        base = _shown(get_url)
        print(f"    GET -> {base}")
        try:
            response = await http.get(get_url)
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"FAIL: presigned GET through {base} got no answer: "
                f"{type(exc).__name__}: {_scrub(str(exc))} -- downloads are broken"
            ) from None
        if response.status_code != httpx.codes.OK:
            raise SystemExit(
                f"FAIL: presigned GET through {base} answered {response.status_code} "
                f"({failure_hint(response)}) -- downloads are broken"
            )
        if response.content != _PROBE_BODY:
            raise SystemExit(
                f"FAIL: presigned GET through {base} returned {len(response.content)} bytes "
                f"that differ from the {len(_PROBE_BODY)} uploaded -- downloads are broken"
            )
        print("[4] presigned GET through the public address returned the same bytes  OK")
    finally:
        await storage.delete(_PROBE_KEY)


async def check_public_roundtrip(
    environ: Mapping[str, str],
    storage: ProbeStorage,
    open_client: Callable[[], httpx.AsyncClient],
) -> None:
    """Check [4]: SKIPPED_LINE, or the reason and then public_roundtrip()."""
    reason = roundtrip_reason(environ)
    if reason is None:
        print(SKIPPED_LINE)
        return
    print(f"    [4] runs: {reason}")
    async with open_client() as http:
        await public_roundtrip(storage, http)


async def main() -> None:
    await check_pgbouncer()
    await check_presign()
    print("\nPASS: pgbouncer/OPS-02, RLS fail-closed, and presign addressing all verified")


if __name__ == "__main__":
    asyncio.run(main())
