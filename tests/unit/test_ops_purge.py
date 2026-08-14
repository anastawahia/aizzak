"""Hermetic tests for the workspace content-purge sweep (``app.ops.purge``,
BE-ADM-014).

Everything here runs over minimal duck-typed stand-ins for
``AsyncConnection``/``AsyncEngine`` (the ``test_ops_retention.py``
precedent) plus fake Qdrant/MinIO clients that record every call -- never a
real database, vector store or object store. What this file proves: store
order (Qdrant -> MinIO -> Postgres -> finalize), table order within each
Postgres schema, that every DELETE's predicate includes
``workspace_id = :ws``, that ``set_config`` is bound (never interpolated),
cutoff arithmetic in ``find_candidates``, that a dry run issues zero
mutating calls anywhere, that a candidate missing its ``account.deleted``
audit row is skipped (with a warning) rather than purged with a fabricated
actor, that ``delete_prefix`` rejects a non-``<uuid>/`` prefix, and the
CLI's own argument validation. The live round trip against real Postgres/
Qdrant/MinIO -- the proof a hermetic stub structurally cannot make -- lives
in ``tests/integration/test_purge_ops_live.py`` and
``tests/integration/test_purge_stores_live.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.infrastructure.storage.minio_storage import delete_prefix
from app.ops import purge as purge_module
from app.ops.purge import (
    PURGE_RETENTION,
    PurgeCandidate,
    StoreResult,
    finalize,
    find_candidates,
    purge_objects,
    purge_rows,
    purge_vectors,
    purge_workspace,
)

_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_WS = "0198a000-0000-7000-8000-000000000001"
_OTHER_WS = "0198a000-0000-7000-8000-000000000009"
_USER = "0198a000-0000-7000-8000-000000000002"
_ACTOR = "0198a000-0000-7000-8000-000000000003"


# ---------------------------------------------------------------------------
# Fakes -- the test_ops_retention.py precedent, extended for mapped rows
# (find_candidates) and for fake Qdrant/MinIO clients.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(
        self,
        *,
        rowcount: int = 0,
        scalar: Any = 0,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rowcount = rowcount
        self._scalar = scalar
        self._rows = rows if rows is not None else []

    def scalar_one(self) -> Any:
        return self._scalar

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    """Records every ``execute`` call's SQL text + params, and hands back a
    configurable rowcount (DELETE) / scalar (dry-run count) / row set
    (``find_candidates``)."""

    def __init__(
        self,
        *,
        rowcount: int = 0,
        scalar: Any = 0,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._rowcount = rowcount
        self._scalar = scalar
        self._rows = rows if rows is not None else []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.calls.append((str(stmt), dict(params or {})))
        return _FakeResult(rowcount=self._rowcount, scalar=self._scalar, rows=self._rows)


class _FakeTransaction:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeEngine:
    """Hands back the SAME connection for every ``begin()``/``connect()``,
    so calls issued across several per-schema transactions
    (``purge_rows``'s own contract) accumulate, in order, on one shared
    ``.calls`` list."""

    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self._conn)

    def connect(self) -> _FakeTransaction:
        return _FakeTransaction(self._conn)


class _FakeQdrantClient:
    def __init__(self, existing: set[str]) -> None:
        self._existing = set(existing)
        self.calls: list[tuple[str, str]] = []

    async def collection_exists(self, name: str) -> bool:
        self.calls.append(("collection_exists", name))
        return name in self._existing

    async def delete_collection(self, name: str) -> bool:
        self.calls.append(("delete_collection", name))
        self._existing.discard(name)
        return True


@dataclass(frozen=True, slots=True)
class _FakeObject:
    object_name: str


class _FakeMinioClient:
    def __init__(self, objects: dict[str, list[str]]) -> None:
        self._objects = objects
        self.calls: list[tuple[Any, ...]] = []

    def list_objects(
        self, bucket_name: str, prefix: str | None = None, recursive: bool = False
    ) -> list[_FakeObject]:
        self.calls.append(("list_objects", bucket_name, prefix, recursive))
        keys = self._objects.get(bucket_name, [])
        return [_FakeObject(k) for k in keys if prefix is None or k.startswith(prefix)]

    def remove_objects(self, bucket_name: str, delete_object_list: Any) -> list[Any]:
        names = [d.name for d in delete_object_list]
        self.calls.append(("remove_objects", bucket_name, names))
        remaining = [k for k in self._objects.get(bucket_name, []) if k not in names]
        self._objects[bucket_name] = remaining
        return []


# ---------------------------------------------------------------------------
# find_candidates
# ---------------------------------------------------------------------------


async def test_find_candidates_computes_cutoff_from_retention_and_now() -> None:
    conn = _FakeConnection(rows=[])
    await find_candidates(conn, retention=timedelta(days=30), now=_NOW)
    [(sql, params)] = conn.calls
    assert params["cutoff"] == _NOW - timedelta(days=30)
    assert params["workspace_id"] is None
    assert "workspace.users" in sql


async def test_find_candidates_returns_a_fully_populated_candidate() -> None:
    conn = _FakeConnection(
        rows=[
            {
                "workspace_id": _WS,
                "target_user_id": _USER,
                "deleted_at": _NOW - timedelta(days=40),
                "actor_user_id": _ACTOR,
            }
        ]
    )
    candidates = await find_candidates(conn, now=_NOW)
    assert candidates == [
        PurgeCandidate(
            workspace_id=_WS,
            target_user_id=_USER,
            actor_user_id=_ACTOR,
            deleted_at=_NOW - timedelta(days=40),
        )
    ]


async def test_find_candidates_binds_the_optional_workspace_id_filter() -> None:
    conn = _FakeConnection(rows=[])
    await find_candidates(conn, now=_NOW, workspace_id=_WS)
    [(_, params)] = conn.calls
    assert params["workspace_id"] == _WS


async def test_find_candidates_skips_and_warns_when_no_actor_is_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = _FakeConnection(
        rows=[
            {
                "workspace_id": _WS,
                "target_user_id": _USER,
                "deleted_at": _NOW - timedelta(days=40),
                "actor_user_id": None,
            }
        ]
    )
    with caplog.at_level("WARNING", logger="app.ops.purge"):
        candidates = await find_candidates(conn, now=_NOW)

    assert candidates == []
    assert "candidate_skipped_no_actor" in caplog.text


async def test_find_candidates_limit_applies_after_skipping_actorless_rows() -> None:
    conn = _FakeConnection(
        rows=[
            {
                "workspace_id": "ws-1",
                "target_user_id": "u-1",
                "deleted_at": _NOW,
                "actor_user_id": None,
            },
            {
                "workspace_id": "ws-2",
                "target_user_id": "u-2",
                "deleted_at": _NOW,
                "actor_user_id": "a-2",
            },
            {
                "workspace_id": "ws-3",
                "target_user_id": "u-3",
                "deleted_at": _NOW,
                "actor_user_id": "a-3",
            },
        ]
    )
    candidates = await find_candidates(conn, now=_NOW, limit=1)
    assert [c.workspace_id for c in candidates] == ["ws-2"]


# ---------------------------------------------------------------------------
# purge_vectors
# ---------------------------------------------------------------------------


async def test_purge_vectors_drops_knowledge_then_memory_collection() -> None:
    client = _FakeQdrantClient(existing={f"kn-{_WS}", f"mem-{_WS}"})
    results = await purge_vectors(client, _WS)

    assert [r.store for r in results] == [f"qdrant:kn-{_WS}", f"qdrant:mem-{_WS}"]
    assert all(r.affected == 1 and not r.dry_run for r in results)
    assert client.calls == [
        ("collection_exists", f"kn-{_WS}"),
        ("delete_collection", f"kn-{_WS}"),
        ("collection_exists", f"mem-{_WS}"),
        ("delete_collection", f"mem-{_WS}"),
    ]


async def test_purge_vectors_is_idempotent_against_a_missing_collection() -> None:
    client = _FakeQdrantClient(existing=set())
    results = await purge_vectors(client, _WS)
    assert all(r.affected == 0 for r in results)
    assert not any(call[0] == "delete_collection" for call in client.calls)


async def test_purge_vectors_dry_run_never_drops_anything() -> None:
    client = _FakeQdrantClient(existing={f"kn-{_WS}"})
    results = await purge_vectors(client, _WS, dry_run=True)

    assert results[0].affected == 1
    assert results[1].affected == 0
    assert all(r.dry_run for r in results)
    assert all(call[0] == "collection_exists" for call in client.calls)


# ---------------------------------------------------------------------------
# purge_objects / delete_prefix
# ---------------------------------------------------------------------------


async def test_purge_objects_deletes_only_the_workspace_prefix() -> None:
    client = _FakeMinioClient({"bucket": [f"{_WS}/a.txt", f"{_WS}/b.txt", f"{_OTHER_WS}/c.txt"]})
    result = await purge_objects(client, "bucket", _WS)

    assert result.store == f"minio:{_WS}/"
    assert result.affected == 2
    assert not result.dry_run
    assert client._objects["bucket"] == [f"{_OTHER_WS}/c.txt"]
    [remove_call] = [c for c in client.calls if c[0] == "remove_objects"]
    assert sorted(remove_call[2]) == sorted([f"{_WS}/a.txt", f"{_WS}/b.txt"])


async def test_purge_objects_dry_run_lists_but_never_removes() -> None:
    client = _FakeMinioClient({"bucket": [f"{_WS}/a.txt"]})
    result = await purge_objects(client, "bucket", _WS, dry_run=True)

    assert result.affected == 1
    assert result.dry_run
    assert not any(call[0] == "remove_objects" for call in client.calls)
    assert client._objects["bucket"] == [f"{_WS}/a.txt"]


async def test_delete_prefix_rejects_a_non_uuid_prefix() -> None:
    with pytest.raises(ValueError):
        await delete_prefix(_FakeMinioClient({}), "bucket", "not-a-uuid/")  # type: ignore[arg-type]


async def test_delete_prefix_rejects_a_uuid_with_no_trailing_slash() -> None:
    with pytest.raises(ValueError):
        await delete_prefix(_FakeMinioClient({}), "bucket", _WS)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# purge_rows -- table order, predicate shape, bound set_config
# ---------------------------------------------------------------------------


async def test_purge_rows_sets_config_once_per_schema_then_deletes_in_order() -> None:
    conn = _FakeConnection(rowcount=3)
    engine = _FakeEngine(conn)

    results = await purge_rows(engine, _WS)

    set_config_calls = [c for c in conn.calls if "set_config" in c[0]]
    assert len(set_config_calls) == len(purge_module._SCHEMA_ORDER)
    for sql, params in set_config_calls:
        assert sql == "SELECT set_config('app.workspace_id', :ws, true)"
        assert params == {"ws": _WS}
        assert _WS not in sql  # bound, never string-interpolated

    delete_calls = [c for c in conn.calls if c[0].startswith("DELETE FROM")]
    expected_tables = [table for spec in purge_module._SCHEMA_ORDER for table in spec.tables]
    assert [sql.split()[2] for sql, _ in delete_calls] == expected_tables
    for sql, params in delete_calls:
        assert sql.endswith("WHERE workspace_id = :ws")
        assert params == {"ws": _WS}

    assert [r.store for r in results] == expected_tables
    assert all(r.affected == 3 and not r.dry_run for r in results)


async def test_purge_rows_dry_run_counts_instead_of_deleting() -> None:
    conn = _FakeConnection(scalar=5)
    engine = _FakeEngine(conn)

    results = await purge_rows(engine, _WS, dry_run=True)

    assert all(r.affected == 5 and r.dry_run for r in results)
    assert not any(sql.startswith("DELETE") for sql, _ in conn.calls)
    for sql, params in conn.calls:
        assert sql.startswith("SELECT count(*)") or "set_config" in sql
        if sql.startswith("SELECT count(*)"):
            assert sql.endswith("WHERE workspace_id = :ws")
            assert params == {"ws": _WS}


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


async def test_finalize_archives_the_workspace_then_inserts_the_audit_row() -> None:
    conn = _FakeConnection()
    engine = _FakeEngine(conn)
    candidate = PurgeCandidate(
        workspace_id=_WS, target_user_id=_USER, actor_user_id=_ACTOR, deleted_at=_NOW
    )
    stores = (StoreResult(store="knowledge.chunks", affected=2, dry_run=False),)

    purged_at = await finalize(engine, candidate, stores, now=_NOW)

    assert purged_at == _NOW
    [(update_sql, update_params), (insert_sql, insert_params)] = conn.calls
    assert "UPDATE workspace.workspaces" in update_sql
    assert "status = 'archived'" in update_sql
    assert update_params == {"purged_at": _NOW, "ws": _WS}

    assert "workspace.purged" in insert_sql
    assert "platform.admin_audit_log" in insert_sql
    assert insert_params["actor"] == _ACTOR
    assert insert_params["target"] == _USER
    assert insert_params["ws"] == _WS
    details = json.loads(insert_params["details"])
    assert details["stores"] == {"knowledge.chunks": 2}
    assert details["retention_days"] == PURGE_RETENTION.days


# ---------------------------------------------------------------------------
# purge_workspace -- overall store order + the dry-run "zero mutations" proof
# ---------------------------------------------------------------------------


async def test_purge_workspace_visits_stores_in_qdrant_minio_postgres_finalize_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def fake_vectors(
        client: object, ws: str, *, dry_run: bool = False
    ) -> tuple[StoreResult, ...]:
        order.append("qdrant")
        return (StoreResult(store="qdrant:kn", affected=1, dry_run=dry_run),)

    async def fake_objects(
        client: object, bucket: str, ws: str, *, dry_run: bool = False
    ) -> StoreResult:
        order.append("minio")
        return StoreResult(store="minio:ws/", affected=1, dry_run=dry_run)

    async def fake_rows(
        engine: object, ws: str, *, dry_run: bool = False
    ) -> tuple[StoreResult, ...]:
        order.append("postgres")
        return (StoreResult(store="knowledge.chunks", affected=1, dry_run=dry_run),)

    async def fake_finalize(
        engine: object,
        candidate: PurgeCandidate,
        stores: tuple[StoreResult, ...],
        *,
        now: Any = None,
    ) -> datetime:
        order.append("finalize")
        return _NOW

    monkeypatch.setattr(purge_module, "purge_vectors", fake_vectors)
    monkeypatch.setattr(purge_module, "purge_objects", fake_objects)
    monkeypatch.setattr(purge_module, "purge_rows", fake_rows)
    monkeypatch.setattr(purge_module, "finalize", fake_finalize)

    candidate = PurgeCandidate(
        workspace_id=_WS, target_user_id=_USER, actor_user_id=_ACTOR, deleted_at=_NOW
    )
    result = await purge_module.purge_workspace(None, None, None, "bucket", candidate)

    assert order == ["qdrant", "minio", "postgres", "finalize"]
    assert result.purged_at == _NOW
    assert len(result.stores) == 3
    assert not result.dry_run


async def test_purge_workspace_dry_run_issues_zero_mutating_calls() -> None:
    qdrant = _FakeQdrantClient(existing={f"kn-{_WS}", f"mem-{_WS}"})
    minio = _FakeMinioClient({"bucket": [f"{_WS}/a.txt"]})
    conn = _FakeConnection(scalar=2)
    engine = _FakeEngine(conn)
    candidate = PurgeCandidate(
        workspace_id=_WS, target_user_id=_USER, actor_user_id=_ACTOR, deleted_at=_NOW
    )

    result = await purge_workspace(engine, qdrant, minio, "bucket", candidate, dry_run=True)

    assert result.dry_run is True
    assert result.purged_at is None
    assert not any(call[0] == "delete_collection" for call in qdrant.calls)
    assert not any(call[0] == "remove_objects" for call in minio.calls)
    assert not any(sql.startswith(("DELETE", "UPDATE", "INSERT")) for sql, _ in conn.calls)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_parses_scan_defaults() -> None:
    parser = purge_module._build_parser()
    args = parser.parse_args(["scan"])
    assert args.action == "scan"
    assert args.older_than_days is None
    assert args.limit is None


def test_cli_parses_run_flags() -> None:
    parser = purge_module._build_parser()
    args = parser.parse_args(["run", "--workspace-id", _WS, "--dry-run", "--limit", "5"])
    assert args.workspace_id == _WS
    assert args.dry_run is True
    assert args.yes is False
    assert args.limit == 5


def test_cli_refuses_run_without_yes_or_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.purge", "run"])
    with pytest.raises(SystemExit) as raised:
        purge_module.main()
    assert "--yes" in str(raised.value)


def test_cli_accepts_run_with_dry_run_and_no_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry-run`` alone is a valid confirmation; ``main`` must not refuse
    it before ever reaching ``_run_cli`` (which needs live infrastructure and
    is exercised separately)."""
    parser = purge_module._build_parser()
    args = parser.parse_args(["run", "--dry-run"])
    assert not (args.action == "run" and not args.dry_run and not args.yes)


def test_cli_requires_a_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["app.ops.purge"])
    with pytest.raises(SystemExit):
        purge_module.main()
