from __future__ import annotations

import json
from datetime import datetime

try:
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, and_, create_engine, insert, select, update
    from sqlalchemy.engine import Engine
except ImportError:  # pragma: no cover - dependency boundary
    Engine = object  # type: ignore[assignment,misc]

from agent_core import State


class ConcurrentStateUpdate(RuntimeError):
    """The stored version changed before this state could be persisted."""


class PostgresStateStore:
    """Synchronous SQLAlchemy StateStore using optimistic locking."""

    def __init__(self, database_url: str, *, table_name: str = "agent_states") -> None:
        try:
            engine = create_engine(database_url)
        except NameError as exc:  # pragma: no cover - dependency boundary
            raise RuntimeError("sqlalchemy is required for PostgresStateStore") from exc
        self.engine: Engine = engine
        metadata = MetaData()
        self.table = Table(
            table_name,
            metadata,
            Column("entity_id", String(255), primary_key=True),
            Column("entity_type", String(255), primary_key=True),
            Column("values_json", Text, nullable=False),
            Column("version", Integer, nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )
        metadata.create_all(self.engine)

    def get(self, entity_id: str, entity_type: str) -> State | None:
        statement = select(self.table).where(
            and_(self.table.c.entity_id == entity_id, self.table.c.entity_type == entity_type)
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        return State(
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            values=json.loads(row["values_json"]),
            version=row["version"],
            updated_at=row["updated_at"],
        )

    def save(self, state: State) -> None:
        values = {
            "entity_id": state.entity_id,
            "entity_type": state.entity_type,
            "values_json": json.dumps(state.values, default=str, sort_keys=True),
            "version": state.version,
            "updated_at": state.updated_at,
        }
        with self.engine.begin() as connection:
            if state.version == 0:
                try:
                    connection.execute(insert(self.table).values(**values))
                    return
                except Exception as exc:
                    raise ConcurrentStateUpdate("State already exists") from exc
            result = connection.execute(
                update(self.table)
                .where(
                    and_(
                        self.table.c.entity_id == state.entity_id,
                        self.table.c.entity_type == state.entity_type,
                        self.table.c.version == state.version - 1,
                    )
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise ConcurrentStateUpdate(
                    f"Expected version {state.version - 1} for {state.entity_type}/{state.entity_id}"
                )
