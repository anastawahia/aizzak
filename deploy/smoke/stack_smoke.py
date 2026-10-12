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
  4. **A presigned PUT through the public address actually stores bytes**
     (RunPod only). [3] reads the URL and never sends it, and on RunPod that
     was not enough: the first staging Pod passed [3] while every browser
     upload failed `403 SignatureDoesNotMatch`, because RunPod's proxy
     rewrites the Host that SigV4 signed (deploy/runpod/nginx.conf,
     `$minio_signed_host`). Only a real round trip through the proxy proves
     it. On Compose the public address is the operator's `localhost`, which
     this container cannot reach, so the check runs only where
     `RUNPOD_POD_ID` says the public address is reachable from inside.
"""

from __future__ import annotations

import asyncio
import os

import httpx
from sqlalchemy import text

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
_PROBE_KEY = "smoke/presign-put-probe.txt"


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

    if not os.environ.get("RUNPOD_POD_ID"):
        print("[4] presigned PUT round trip: skipped (not on RunPod)")
        return
    put_url = await storage.presign_put(_PROBE_KEY, 300, "text/plain")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.put(
                put_url, content=b"stack_smoke probe\n", headers={"Content-Type": "text/plain"}
            )
        if response.status_code != httpx.codes.OK:
            code = "SignatureDoesNotMatch" if "SignatureDoesNotMatch" in response.text else ""
            raise SystemExit(
                f"FAIL: presigned PUT through {put_url.split('?')[0]} answered "
                f"{response.status_code} {code} -- browser uploads are broken"
            )
        if await storage.get(_PROBE_KEY) != b"stack_smoke probe\n":
            raise SystemExit("FAIL: presigned PUT answered 200 but the object is not in MinIO")
    finally:
        await storage.delete(_PROBE_KEY)
    print("[4] presigned PUT through the public address stored the bytes  OK")


async def main() -> None:
    await check_pgbouncer()
    await check_presign()
    print("\nPASS: pgbouncer/OPS-02, RLS fail-closed, and presign addressing all verified")


if __name__ == "__main__":
    asyncio.run(main())
