from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table, inspect

from agent_enterprise import migrate_enterprise_authorization
from agent_postgres import PostgresAtomicOperationStore, PostgresMigrationRunner, SchemaMigration, migrate_agent_postgres


def test_migration_runner_records_each_version_once(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrations.db'}"
    applied: list[str] = []
    runner = PostgresMigrationRunner(database_url, component="test_component")
    migrations = (
        SchemaMigration(1, "first", lambda _: applied.append("first")),
        SchemaMigration(2, "second", lambda _: applied.append("second")),
    )

    assert runner.upgrade(migrations) == [1, 2]
    assert applied == ["first", "second"]
    assert runner.upgrade(migrations) == []
    assert applied == ["first", "second"]


def test_migration_upgrades_a_legacy_outbox_without_lease_columns(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrations.db'}"
    metadata = MetaData()
    # This matches the pre-HA outbox shape. A deployed table is not changed by
    # SQLAlchemy create_all, so the migration must add the missing columns.
    Table(
        "legacy_outbox", metadata,
        Column("event_id", String(255), primary_key=True),
        Column("operation_id", String(255), nullable=False, unique=True),
        Column("event_type", String(255), nullable=False),
        Column("entity_id", String(255), nullable=False),
        Column("entity_type", String(255), nullable=False),
        Column("payload_json", String, nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("source", String(255)),
        Column("metadata_json", String, nullable=False),
        Column("idempotency_key", String(255)),
        Column("status", String(16), nullable=False),
        Column("published_at", DateTime(timezone=True)),
    )
    from sqlalchemy import create_engine
    engine = create_engine(database_url)
    metadata.create_all(engine)

    assert migrate_agent_postgres(database_url, table_prefix="legacy") == [1, 2]
    columns = {column["name"] for column in inspect(engine).get_columns("legacy_outbox")}
    assert {"lease_owner", "lease_expires_at", "publish_attempts"} <= columns
    assert migrate_agent_postgres(database_url, table_prefix="legacy") == []
    # The current adapter can use the migrated deployment schema.
    assert PostgresAtomicOperationStore(database_url, table_prefix="legacy").outbox.name == "legacy_outbox"


def test_enterprise_migration_records_replay_and_revocation_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'enterprise.db'}"
    assert migrate_enterprise_authorization(database_url) == [1]
    assert migrate_enterprise_authorization(database_url) == []


def test_independently_named_adapter_schemas_have_independent_versions(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'multiple.db'}"
    assert migrate_agent_postgres(database_url, table_prefix="orders") == [1, 2]
    assert migrate_agent_postgres(database_url, table_prefix="inventory") == [1, 2]
