"""Every ``Table`` an electrical section's adapters declare carries its section's prefix and
lives in the ``electrical`` schema (BT36; ``INC1-D-23``, INC1-AC-12).

One schema for the whole container, one prefix per section (``drawings_*``, later ``circuits_*``,
``search_*``, ``diagnostics_*``), so two sections can never claim a table name and a table is
traceable to its owner by name alone. The scan reads the adapters' SOURCE with ``ast`` -- a
``Table("drawings_jobs", metadata, ..., schema=...)`` call -- so it needs no database and
catches a table declared in a helper module just as well.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ELECTRICAL = Path(__file__).resolve().parents[2] / "src" / "app" / "modules" / "electrical"
SECTIONS = ("drawings", "circuits", "search", "diagnostics")
SCHEMA = "electrical"


def _tables(section: str) -> list[tuple[Path, str, str | None]]:
    """(file, table name, schema) of every ``Table(...)`` call under the section's adapters."""
    found: list[tuple[Path, str, str | None]] = []
    for path in sorted((ELECTRICAL / section / "adapters").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_table(node.func) and node.args):
                continue
            name = _string(node.args[0], constants)
            schema = next(
                (_string(k.value, constants) for k in node.keywords if k.arg == "schema"), None
            )
            assert name is not None, f"{path}: a Table whose name is not a literal"
            found.append((path, name, schema))
    return found


def _constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` and ``NAME: Final = "literal"``."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.update({t.id: value.value for t in targets if isinstance(t, ast.Name)})
    return out


def _is_table(func: ast.expr) -> bool:
    return (isinstance(func, ast.Name) and func.id == "Table") or (
        isinstance(func, ast.Attribute) and func.attr == "Table"
    )


def _string(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def test_the_scan_finds_the_drawings_tables() -> None:
    """Guard the guard: a scan that finds nothing would pass every check below."""
    names = {name for _, name, _ in _tables("drawings")}
    assert names == {
        "drawings_projects",
        "drawings_documents",
        "drawings_jobs",
        "drawings_pages",
        "drawings_page_stages",
        "drawings_profile_versions",
        "drawings_characterizations",
    }


@pytest.mark.parametrize("section", SECTIONS)
def test_every_table_carries_its_sections_prefix_and_the_electrical_schema(section: str) -> None:
    for path, name, schema in _tables(section):
        assert name.startswith(f"{section}_"), f"{path.name}: {name!r} lacks '{section}_'"
        assert schema == SCHEMA, f"{path.name}: {name!r} is in schema {schema!r}, not {SCHEMA!r}"


def test_no_table_name_is_claimed_by_two_sections() -> None:
    owners: dict[str, str] = {}
    for section in SECTIONS:
        for _, name, _ in _tables(section):
            assert name not in owners, f"{name} is declared by {owners[name]} and {section}"
            owners[name] = section


def test_sections_other_than_drawings_declare_no_table_yet() -> None:
    """INC-1 has one section with tables. This changes deliberately, with the increment that
    adds the next section's first table (and its prefix check above)."""
    assert [t for s in SECTIONS if s != "drawings" for t in _tables(s)] == []
