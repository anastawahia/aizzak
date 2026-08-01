"""Transit key-rotation sweep (P1-9, ``docs/p1-hardening-plan.md`` §3 step
12): rewraps every stored ciphertext under the Vault Transit key's CURRENT
version and PERSISTS the result. Rewrapping without persisting is a no-op --
Vault's own ``rewrap`` call never touches anything outside the request/
response it answers, so the only place "the ciphertext is now current" can
possibly become true is a database row this module writes.

**What this tool is not.** It never mints a new Transit key version and
never advances ``min_decryption_version`` -- both are Vault key-ADMINISTRATION
actions (``transit/keys/tenant-secrets/rotate`` and
``transit/keys/tenant-secrets/config``), a strictly bigger privilege than
"rewrap under whatever version already exists" that ``deploy/vault/
app-policy.hcl`` deliberately does NOT grant the application's AppRole (see
that file's own "Deliberately NOT granted" section). Both are operator
actions run directly against Vault with an admin credential, in the sequence
``08-local-runbook.md`` §4.5 gives; this tool is the middle step of that
sequence, run AFTER the operator rotates the key and BEFORE the operator
(irreversibly) advances ``min_decryption_version``.

**The three ciphertext-bearing columns** (confirmed by grepping every
``SecretsProvider.encrypt`` call site, 05-rbac-config-secrets §3.2's unified
``tenant-secrets`` key): ``credentials.credentials.ciphertext_ref``
(``migrations/versions/credentials/0001_credentials.py:59`` -- NOT NULL,
every row has one), ``integrations.connections.token_ref``
(``.../integrations/0001_integrations.py:46`` -- NULLABLE: a ``pending``
connection has no token yet), and ``integrations.mcp_servers.auth_ref``
(``.../integrations/0001_integrations.py:74`` -- NULLABLE: ``auth`` is an
optional argument to ``RegisterMcpServer``). Each row also carries a
``key_id`` column holding the Transit key NAME that encrypted it (currently
always ``"tenant-secrets"``, SEC-07's one unified key) -- this sweep reads
that column back and rewraps under the NAME the row itself names, rather
than hardcoding the literal, so it keeps working unchanged if a second
Transit key is ever introduced.

**The RLS wall -- the identical problem ``app.ops.retention`` hit, resolved
the identical way.** A rotation sweep runs across every tenant, and
``app_rw``'s own tenant confinement (RLS, not the grant) would otherwise
limit this process to whatever single workspace its own session happens to
have set -- nothing, since this process never sets one. Widening `app_rw`
to reach every row would undo the tenant-isolation guarantee the whole
platform is built on, so this module never runs as `app_rw`. Instead:
``transit_rotator`` (``app.ops.provision.TRANSIT_ROTATOR_ROLE``) is its OWN
role, holding SELECT plus column-scoped UPDATE (the ciphertext column
alone -- never `status`/`label`/`scopes`, so a forged value can only break
decryption of the ONE secret it targets, never misrepresent a row's
identity or state) on the three tables above
(``app.ops.provision.TRANSIT_ROTATOR_GRANTS``), and two migrations
(``migrations/versions/credentials/0002_transit_rotator.py``,
``migrations/versions/integrations/0002_transit_rotator.py``) add
permissive, role-scoped RLS policies (``USING (true)``, OR-combined with
each table's own ``tenant_isolation``) so this ONE role reaches every
workspace's row while every other role's confinement stays untouched.

**Idempotency -- measured, not assumed, and the obvious approach is wrong.**
The tempting check is "did Vault's ``rewrap`` return the same ciphertext
bytes back?" -- measured live against a real Vault dev server, it does NOT:
Vault's AES-GCM mode picks a fresh nonce on every single ``rewrap`` call
regardless of whether the key version actually changed, so calling
``rewrap`` twice on the SAME input with no rotation in between returns two
DIFFERENT ciphertext strings, neither equal to the input. Byte comparison
would therefore report every row as "needs rewrapping" on every run,
forever -- the opposite of idempotent. What stays constant across a no-op
rewrap is the Transit key VERSION NUMBER embedded in Vault's own
``vault:v<n>:...`` wire prefix (see ``VaultSecrets.rewrap``'s own docstring).
``_key_version`` parses that prefix (never decrypts anything), and a row is
only written back when the rewrapped ciphertext's version is STRICTLY
GREATER than the stored one's -- i.e. an operator actually rotated the key
forward since this ciphertext was last touched.

**Safe to interrupt at any point.** Each row's read (SELECT), Vault call,
and write (UPDATE) commit in their OWN short transaction -- deliberately NOT
one transaction per table the way ``app.ops.retention``'s single-statement
``DELETE``/``SELECT count(*)`` is: retention's SQL has no I/O interleaved
inside its transaction, but this sweep's per-row unit of work is a blocking
Vault round trip, and holding a Postgres transaction open across many of
those would hold locks and a pooled connection for the length of an entire
table's sweep for no correctness benefit. If the process is interrupted
(killed, network partition, ...) at any point: every row already committed
stays at the newer version; every row not yet reached is untouched and
picked up by running this tool again; and any row Vault-side-rewrapped but
not yet persisted before the interruption is simply re-read, re-rewrapped (a
cheap, side-effect-free Vault call that produces a fresh ciphertext at the
SAME newer version every time) and compared again on the next run -- there
is no partial, corrupt, or double-write state this sweep can leave behind.
Concurrent modification (a tenant's own request re-encrypting the SAME row
between this sweep's ``SELECT`` and its ``UPDATE`` -- a credential rotation,
an OAuth token refresh) is guarded explicitly: the ``UPDATE`` is qualified
``WHERE ... AND <column> = :old``, so a row that changed underneath the
sweep is left alone (not counted as rewrapped) rather than clobbering
whatever the concurrent write just stored.

Usage::

    python -m app.ops.rotate_transit sweep [--dry-run]

``--dry-run`` still calls Vault's real ``rewrap`` for every row (it has no
side effect on Vault or on Postgres -- see the module docstring above) so
the reported count is exact, but issues no ``UPDATE`` at all.

``DATABASE_URL`` for THIS process must be the ``transit_rotator`` role's OWN
DSN -- the same per-process convention ``provision.py``/``retention.py``
already document for ``aizzak_owner``/``retention_sweeper``: one role per
process, never one role wearing another's hat.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings, load_vault_auth
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.secrets.vault_secrets import (
    VaultSecrets,
    create_approle_relogin,
    create_vault_client,
)

_logger = logging.getLogger(__name__)

# Vault Transit's own wire prefix (``VaultSecrets``'s module docstring,
# ``vault:v<n>:<base64>``) -- the ONE part of a ciphertext string that stays
# constant across a no-op rewrap, unlike the bytes themselves (module
# docstring's "Idempotency" paragraph).
_VERSION_RE = re.compile(r"^vault:v(\d+):")


def _key_version(ciphertext: str) -> int:
    """Parse the Transit key version out of Vault's own ciphertext prefix,
    never decrypting anything. Raises ``ValueError`` (folded into
    ``common.internal`` by nothing here -- this tool is a CLI, not a request
    handler) on a string that is not Vault Transit ciphertext at all, which
    would itself mean this table holds data no ``SecretsProvider.encrypt``
    call ever produced -- a data-integrity fault worth crashing loudly on
    rather than silently miscounting."""
    match = _VERSION_RE.match(ciphertext)
    if match is None:
        raise ValueError(f"not a Vault Transit ciphertext (no {_VERSION_RE.pattern!r} prefix)")
    return int(match.group(1))


@dataclass(frozen=True, slots=True)
class _TableSpec:
    """One ciphertext-bearing table this sweep reaches. ``id_col``/
    ``cipher_col``/``key_col`` are hardcoded literals from this tuple alone
    (never user input), matching every other ``app.ops.*`` module's own
    literal-SQL convention (``provision.py``'s schema names,
    ``retention.py``'s table names)."""

    table: str  # "credentials.credentials"
    id_col: str
    cipher_col: str
    key_col: str


_TABLE_SPECS: tuple[_TableSpec, ...] = (
    _TableSpec("credentials.credentials", "id", "ciphertext_ref", "key_id"),
    _TableSpec("integrations.connections", "id", "token_ref", "key_id"),
    _TableSpec("integrations.mcp_servers", "id", "auth_ref", "key_id"),
)


@dataclass(frozen=True, slots=True)
class RewrapResult:
    """One table's sweep outcome. ``scanned`` is every row with a non-NULL
    ciphertext column; ``rewrapped`` is how many of those were found at a
    STRICTLY OLDER key version than Vault's rewrap just produced (module
    docstring's ``_key_version`` comparison) -- in a real (non-dry-run)
    sweep this only counts a row whose ``UPDATE`` actually affected one,
    never a row a concurrent write raced away underneath the sweep."""

    table: str
    scanned: int
    rewrapped: int
    dry_run: bool


async def rewrap_table(
    engine: AsyncEngine, secrets: VaultSecrets, spec: _TableSpec, *, dry_run: bool = False
) -> RewrapResult:
    """Sweep one table. The SELECT runs on its own short-lived connection
    (module docstring: no Vault I/O happens inside a held transaction), and
    each row that actually changes commits its own ``UPDATE`` immediately --
    row-level resumability, not table-level."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    f"SELECT {spec.id_col}, {spec.cipher_col}, {spec.key_col} "
                    f"FROM {spec.table} WHERE {spec.cipher_col} IS NOT NULL"
                )
            )
        ).all()

    rewrapped = 0
    for row_id, ciphertext, key_name in rows:
        new_ciphertext = await secrets.rewrap(key_name, ciphertext)
        if _key_version(new_ciphertext) <= _key_version(ciphertext):
            continue  # already at the current key version -- no-op, re-run safe
        if dry_run:
            rewrapped += 1
            continue
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    f"UPDATE {spec.table} SET {spec.cipher_col} = :new "
                    f"WHERE {spec.id_col} = :id AND {spec.cipher_col} = :old"
                ),
                {"new": new_ciphertext, "id": row_id, "old": ciphertext},
            )
        if result.rowcount:
            rewrapped += 1
        else:
            # A concurrent write (credential rotation, OAuth refresh) changed
            # this row between the SELECT above and this UPDATE -- left
            # alone deliberately (module docstring), not an error.
            _logger.info(
                "rotate_transit.row_changed_concurrently",
                extra={"table": spec.table, "id": row_id},
            )
    return RewrapResult(table=spec.table, scanned=len(rows), rewrapped=rewrapped, dry_run=dry_run)


async def rewrap_all(
    engine: AsyncEngine, secrets: VaultSecrets, *, dry_run: bool = False
) -> list[RewrapResult]:
    """Sweep all three tables in order. Each table is fully independent of
    the others (different schemas, no shared row), so nothing couples their
    outcomes -- the ``retention.py sweep_all`` precedent."""
    return [await rewrap_table(engine, secrets, spec, dry_run=dry_run) for spec in _TABLE_SPECS]


def _print_result(result: RewrapResult) -> None:
    payload = {
        "table": result.table,
        "scanned": result.scanned,
        "rewrapped": result.rewrapped,
        "dry_run": result.dry_run,
    }
    print(json.dumps(payload, ensure_ascii=False))
    _logger.info("ops.rotate_transit.swept", extra=payload)


async def _run_cli(args: argparse.Namespace) -> int:
    settings = load_settings()
    vault_auth = load_vault_auth()
    vault_client = create_vault_client(
        settings.vault, token=vault_auth.token, secret_id=vault_auth.secret_id
    )
    secrets = VaultSecrets(
        vault_client,
        relogin=(
            create_approle_relogin(settings.vault, vault_client, secret_id=vault_auth.secret_id)
            if not vault_auth.token
            else None
        ),
    )
    engine = create_engine(DatabaseSettings(url=settings.database.url), poolclass=NullPool)
    try:
        for result in await rewrap_all(engine, secrets, dry_run=args.dry_run):
            _print_result(result)
        return 0
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.rotate_transit",
        description="Rewrap every stored Transit ciphertext under the key's CURRENT "
        "version and persist the result (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sweep_parser = sub.add_parser(
        "sweep",
        help="rewrap+persist every ciphertext across all three tables",
    )
    sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="call Vault's real rewrap and report what WOULD change; write nothing to Postgres",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run_cli(args)))


if __name__ == "__main__":
    main()
