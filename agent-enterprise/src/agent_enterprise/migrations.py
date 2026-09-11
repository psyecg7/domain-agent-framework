"""Explicit provisioning migration for Enterprise PostgreSQL authorization tables."""

from __future__ import annotations

from agent_postgres import PostgresMigrationRunner, SchemaMigration


def migrate_enterprise_authorization(
    database_url: str,
    *,
    replay_table: str = "agent_authorization_replay",
    revocation_table: str = "agent_authorization_revocations",
) -> list[int]:
    """Provision and record the replay/revocation schema version.

    The current Enterprise authorization tables have no historical column
    changes, but recording their initial version makes later upgrades explicit
    rather than relying on runtime ``create_all`` behavior.
    """
    from .postgres_authorization import PostgresReplayStore, PostgresRevocationStore

    PostgresReplayStore(database_url, table_name=replay_table)
    PostgresRevocationStore(database_url, table_name=revocation_table)
    component = f"agent_enterprise_authorization_{replay_table}_{revocation_table}"
    return PostgresMigrationRunner(database_url, component=component).upgrade((
        SchemaMigration(1, "provision replay and revocation tables", lambda _: None),
    ))


__all__ = ["migrate_enterprise_authorization"]
