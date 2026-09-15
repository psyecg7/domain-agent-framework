from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
from collections.abc import Iterator
from datetime import datetime

try:
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, and_, create_engine, insert, select, update
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
except ImportError:  # pragma: no cover - dependency boundary
    Engine = object  # type: ignore[assignment,misc]
    Connection = object  # type: ignore[assignment,misc]

from agent_core import State


class ConcurrentStateUpdate(RuntimeError):
    """The stored version changed before this state could be persisted."""


class StateStoreUnavailable(RuntimeError):
    """PostgreSQL state storage could not be reached or committed."""


class PostgresStateStore:
    """Synchronous SQLAlchemy StateStore using optimistic locking."""

    def __init__(self, database_url: str, *, table_name: str = "agent_states") -> None:
        try:
            engine = create_engine(database_url)
        except NameError as exc:  # pragma: no cover - dependency boundary
            raise RuntimeError("sqlalchemy is required for PostgresStateStore") from exc
        self.engine: Engine = engine
        self._active_connection: ContextVar[Connection | None] = ContextVar(
            f"postgres_state_store_connection_{id(self)}", default=None,
        )
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
        try:
            metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise StateStoreUnavailable("PostgreSQL state storage is unavailable") from exc

    def get(self, entity_id: str, entity_type: str) -> State | None:
        statement = select(self.table).where(
            and_(self.table.c.entity_id == entity_id, self.table.c.entity_type == entity_type)
        )
        try:
            connection = self._active_connection.get()
            if connection is None:
                with self.engine.connect() as read_connection:
                    row = read_connection.execute(statement).mappings().first()
            else:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            raise StateStoreUnavailable("PostgreSQL state storage is unavailable") from exc
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
        try:
            connection = self._active_connection.get()
            if connection is None:
                with self.engine.begin() as write_connection:
                    self._save_on_connection(write_connection, state, values)
            else:
                self._save_on_connection(connection, state, values)
        except IntegrityError as exc:
            if state.version in (0, 1):
                raise ConcurrentStateUpdate("State already exists") from exc
            raise StateStoreUnavailable("PostgreSQL state storage rejected an unexpected write") from exc
        except SQLAlchemyError as exc:
            raise StateStoreUnavailable("PostgreSQL state storage is unavailable") from exc

    @contextmanager
    def use_connection(self, connection: Connection) -> Iterator[None]:
        """Use a caller-owned transaction for this adapter's get/save calls.

        This is adapter composition support, not part of the generic
        ``StateStore`` port. It lets a PostgreSQL receipt runner make an Agent
        state update and an inbound-event receipt one database transaction.
        """
        if not isinstance(connection, Connection):
            raise TypeError("connection must be a SQLAlchemy Connection")
        token = self._active_connection.set(connection)
        try:
            yield
        finally:
            self._active_connection.reset(token)

    def _save_on_connection(self, connection: Connection, state: State, values: dict[str, object]) -> None:
        if state.version == 0:
            connection.execute(insert(self.table).values(**values))
            return
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
        if result.rowcount == 1:
            return
        if state.version == 1:
            # A fresh Agent starts at version zero, applies its first
            # observation, then persists version one. Claim that first row by
            # primary key so concurrent first observations still have one
            # winner rather than silently overwriting each other.
            connection.execute(insert(self.table).values(**values))
            return
        raise ConcurrentStateUpdate(
            f"Expected version {state.version - 1} for {state.entity_type}/{state.entity_id}"
        )
