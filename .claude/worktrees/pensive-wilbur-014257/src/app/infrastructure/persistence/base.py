"""Shared persistence primitives for future INFRASTRUCTURE-owned tables.

Every *module* SQL adapter declares its own local Core ``Table`` against its
own ``MetaData`` (12-module-authoring-guide; the ``media`` precedent in
``modules/media/adapters/sql_repository.py``) — module tables never share
metadata with each other or with this module, keeping modules independent
(``.importlinter`` contract 4). This file is NOT used by ``media`` (or any
module adapter); it exists purely as a narrow, optional convenience for
tables that ``infrastructure/`` itself might own one day (e.g. a future
outbox-relay read model over ``platform.*``) — a place to keep their naming
convention and common column types consistent without inventing a shared ORM
base that would couple modules together through table metadata.
"""

from __future__ import annotations

from sqlalchemy import DateTime, Uuid

# Alembic/SQLAlchemy constraint-naming convention (stable, predictable
# constraint/index names for any infra-owned ``MetaData`` that opts in).
# Module migrations name their own constraints explicitly instead
# (01-data-model.md) and do not use this.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Reusable column-type aliases (DD-02/DD-03): UUIDv7 identifiers are stored
# as native ``uuid`` columns but handled as plain ``str`` in Python
# (``as_uuid=False`` — matches ``app.framework.types.Uuid``); timestamps are
# always timezone-aware (``timestamptz``).
uuid_column_type = Uuid(as_uuid=False)
timestamp_column_type = DateTime(timezone=True)
