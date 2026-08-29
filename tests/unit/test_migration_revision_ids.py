"""Every Alembic revision identifier fits the column that stores it.

Alembic stamps the revision it just applied into ``alembic_version.version_num``,
a column it creates itself as ``VARCHAR(32)``. ``migrations/env.py`` sets
``version_table_schema`` per module (DAT-03) but never widens that column, so
the default width is the one every chain in this repository gets.

The failure this guards is worse than a long name. A revision id over 32
characters **applies its DDL and then fails to record itself**: the schema
change runs, the ``UPDATE alembic_version`` raises
``StringDataRightTruncationError``, and the transaction rolls back the whole
step. Provisioning stops there, so every migration *after* the offender never
runs either -- one name too long halts the chain.

It is invisible to the five gates. Nothing in the unit suite executes Alembic
against Postgres: ``test_conversations_schema_parity.py`` reads these same
files as *text* and says so in its own docstring ("Whether Postgres accepts
the DDL is an integration question"). So a 39-character id passed format,
lint, mypy, import-linter and the full unit run untouched, and surfaced only
in CI's ``integration`` job, at ``docker compose run migrate``, after the
merge.

Hence a unit test rather than an integration one: the constraint is a property
of the *name*, knowable from the source alone, and it should fail in the
cheapest gate that can see it rather than in the most expensive one that does.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VERSIONS = _ROOT / "migrations" / "versions"

# Alembic's own default, from ``alembic.runtime.migration``. Widening the
# column is possible via ``version_table`` options; this repository does not,
# and this constant is the assertion's whole reason to exist -- if a chain ever
# does widen it, this test is where that decision gets recorded.
_VERSION_NUM_WIDTH = 32

_REVISION = re.compile(r"^revision\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def _revisions() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in sorted(_VERSIONS.rglob("*.py")):
        match = _REVISION.search(path.read_text(encoding="utf-8"))
        if match is not None:
            found.append((path, match.group(1)))
    return found


def test_the_migration_tree_has_revisions_to_check() -> None:
    """A regex that silently matches nothing would make this file vacuous."""
    revisions = _revisions()
    assert len(revisions) > 20, (
        f"expected the migration chains under {_VERSIONS} to yield revision "
        f"identifiers; found {len(revisions)} -- the pattern or the layout moved"
    )


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    too_long = [
        (path.relative_to(_ROOT), revision, len(revision))
        for path, revision in _revisions()
        if len(revision) > _VERSION_NUM_WIDTH
    ]
    offenders = "\n".join(
        f"  {path}: {revision!r} is {length} characters" for path, revision, length in too_long
    )
    assert not too_long, (
        f"revision identifiers longer than {_VERSION_NUM_WIDTH} characters cannot "
        "be stamped into `alembic_version.version_num`; the migration applies its "
        f"DDL and then rolls back on the stamp, halting the chain:\n{offenders}"
    )
