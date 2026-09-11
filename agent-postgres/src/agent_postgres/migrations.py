"""Explicit versioned schema upgrades for PostgreSQL adapter deployments."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, inspect, insert, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError


class MigrationUnavailable(RuntimeError):
    """A schema migration could not be read or committed."""


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    description: str
    apply: Callable[[Connection], None]


class PostgresMigrationRunner:
    """Record and apply explicit component-scoped migrations exactly once."""

    def __init__(self, database_url: str, *, component: str, table_name: str = "agent_schema_migrations") -> None:
        if not isinstance(component, str) or not component.isidentifier():
            raise ValueError("component must be a SQL identifier")
        if not isinstance(table_name, str) or not table_name.isidentifier():
            raise ValueError("table_name must be a SQL identifier")
        self.component = component
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.versions = Table(
                table_name, metadata,
                Column("component", String(255), primary_key=True),
                Column("version", Integer, primary_key=True),
                Column("applied_at", DateTime(timezone=True), nullable=False),
            )
            metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise MigrationUnavailable("Schema migration storage is unavailable") from exc

    def upgrade(self, migrations: Iterable[SchemaMigration]) -> list[int]:
        planned = sorted(migrations, key=lambda migration: migration.version)
        if any(not isinstance(item, SchemaMigration) for item in planned):
            raise TypeError("migrations must contain SchemaMigration values")
        if any(item.version < 1 for item in planned) or len({item.version for item in planned}) != len(planned):
            raise ValueError("migration versions must be unique positive integers")
        try:
            with self.engine.begin() as connection:
                applied = set(connection.execute(
                    select(self.versions.c.version).where(self.versions.c.component == self.component)
                ).scalars())
                completed: list[int] = []
                for migration in planned:
                    if migration.version in applied:
                        continue
                    migration.apply(connection)
                    connection.execute(insert(self.versions).values(
                        component=self.component, version=migration.version, applied_at=datetime.now(timezone.utc),
                    ))
                    completed.append(migration.version)
                return completed
        except SQLAlchemyError as exc:
            raise MigrationUnavailable("Schema migration failed") from exc


def migrate_agent_postgres(
    database_url: str,
    *,
    table_prefix: str = "agent_atomic",
    state_table: str = "agent_states",
    receipt_table: str = "agent_event_receipts",
) -> list[int]:
    """Provision current adapter tables and upgrade legacy atomic outboxes."""
    from .atomicity import PostgresAtomicOperationStore
    from .receipts import PostgresEventReceiptStore
    from .store import PostgresStateStore

    atomic = PostgresAtomicOperationStore(database_url, table_prefix=table_prefix)
    PostgresStateStore(database_url, table_name=state_table)
    PostgresEventReceiptStore(database_url, table_name=receipt_table)

    def add_outbox_leases(connection: Connection) -> None:
        columns = {column["name"] for column in inspect(connection).get_columns(atomic.outbox.name)}
        additions = (
            ("lease_owner", "VARCHAR(255)"),
            ("lease_expires_at", "TIMESTAMP WITH TIME ZONE"),
            ("publish_attempts", "INTEGER NOT NULL DEFAULT 0"),
        )
        for name, declaration in additions:
            if name not in columns:
                connection.execute(text(f"ALTER TABLE {atomic.outbox.name} ADD COLUMN {name} {declaration}"))

    # One database can host multiple independently named adapter deployments;
    # track their outbox upgrade separately rather than letting one prefix mark
    # another prefix's schema as current.
    return PostgresMigrationRunner(database_url, component=f"agent_postgres_{table_prefix}").upgrade((
        SchemaMigration(1, "provision state, atomic operation, outbox, and receipt tables", lambda _: None),
        SchemaMigration(2, "add HA outbox lease columns", add_outbox_leases),
    ))


__all__ = ["MigrationUnavailable", "PostgresMigrationRunner", "SchemaMigration", "migrate_agent_postgres"]
