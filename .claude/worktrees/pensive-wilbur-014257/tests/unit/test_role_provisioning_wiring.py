"""Every DB role `app.ops.provision._require_roles` checks for must exist in
the SAME four places: the role name itself (`app.ops.provision`'s own
constants), the actual `CREATE ROLE` in
`deploy/postgres/initdb/10-roles.sh`, the password env var Compose requires
under `:?...` (so a fresh `docker compose up` fails loudly rather than
handing `10-roles.sh` an empty password), and the `.env.example` line that
supplies it for every documented deployment path -- `docker-compose.yml`
auto-loads `.env`, and every deployment guide's first step is
`cp .env.example .env` (the `test_deploy_worker_default.py` precedent, step
3, for exactly why `.env.example` is not optional to check alongside
`docker-compose.yml`).

**The regression this guard exists to catch mechanically.** Discovered in
the coordinator's review of step 8 (P1-5, `docs/p1-hardening-plan.md` §3):
`retention_sweeper` was added to `_require_roles` -- which runs BEFORE any
migration -- without a matching `CREATE ROLE` anywhere under `deploy/`, so
`app.ops.provision.provision()` against a brand-new cluster would fail
("role(s) retention_sweeper do not exist") before migrating a single table.
Before this step nothing compared these four sources; this module does.
"""

from __future__ import annotations

from pathlib import Path

from app.ops.provision import (
    APP_ROLE,
    METRICS_ROLE,
    RELAY_ROLE,
    RETENTION_ROLE,
    TRANSIT_ROTATOR_ROLE,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROLES_SH = _REPO_ROOT / "deploy" / "postgres" / "initdb" / "10-roles.sh"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

# The five LOGIN roles `provision()` verifies exist before running any
# migration (`asyncio.run(_require_roles(owner_url, (APP_ROLE, RELAY_ROLE,
# RETENTION_ROLE, METRICS_ROLE, TRANSIT_ROTATOR_ROLE)))` -- P1-3/step 10 added
# the fourth, P1-9/step 12 added the fifth). `aizzak_owner` is deliberately
# NOT here: it is the DSN `provision()` itself connects as, never looked up by
# name through `_require_roles`, so it has no equivalent entry to drift.
_CHECKED_ROLES = (APP_ROLE, RELAY_ROLE, RETENTION_ROLE, METRICS_ROLE, TRANSIT_ROTATOR_ROLE)


def _password_var(role: str) -> str:
    """`app_rw` -> `APP_RW_PASSWORD`, `outbox_relay` -> `OUTBOX_RELAY_PASSWORD`,
    `retention_sweeper` -> `RETENTION_SWEEPER_PASSWORD` -- the naming
    convention every existing password variable already follows."""
    return f"{role.upper()}_PASSWORD"


def test_the_checked_roles_are_not_accidentally_empty() -> None:
    """A guard iterating an empty tuple would pass forever while checking
    nothing (the 3.69 lesson, restated in `test_ops_provision.py` and
    `test_deploy_worker_default.py`)."""
    assert len(_CHECKED_ROLES) == 5
    assert len(set(_CHECKED_ROLES)) == 5, "the five checked roles must be distinct"


def test_every_required_role_has_a_create_role_statement() -> None:
    text = _ROLES_SH.read_text(encoding="utf-8")
    missing = [role for role in _CHECKED_ROLES if f"CREATE ROLE {role} " not in text]
    assert not missing, (
        f"role(s) {missing} are checked by app.ops.provision._require_roles "
        "before any migration runs, but deploy/postgres/initdb/10-roles.sh has "
        "no `CREATE ROLE <name> ...` for them -- provision() against a fresh "
        "cluster would fail before migrating anything."
    )


def test_every_required_role_has_a_required_compose_password() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    missing = [
        role
        for role in _CHECKED_ROLES
        if f"{_password_var(role)}: ${{{_password_var(role)}:?" not in text
    ]
    assert not missing, (
        f"role(s) {missing} have no `{{ROLE}}_PASSWORD: ${{...:?...}}` line in "
        "docker-compose.yml's postgres service -- deploy/postgres/initdb/"
        "10-roles.sh would receive an empty password for them on a fresh volume."
    )


def test_every_required_role_has_an_env_example_line() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [role for role in _CHECKED_ROLES if f"{_password_var(role)}=" not in text]
    assert not missing, (
        f"role(s) {missing} have no `{{ROLE}}_PASSWORD=` line in .env.example -- "
        "every documented deployment guide copies this file to `.env` as step "
        "one (test_deploy_worker_default.py's precedent), so Compose would fail "
        "with a `:?` error for anyone following the docs."
    )
