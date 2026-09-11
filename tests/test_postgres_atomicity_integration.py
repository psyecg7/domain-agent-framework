"""Opt-in PostgreSQL proof of cross-worker operation claiming."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import os
import uuid

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, delete, inspect, insert, select

from agent_core import Event
from agent_postgres import PostgresAtomicOperationStore, PostgresEventReceiptStore, PostgresMigrationRunner, migrate_agent_postgres
from agent_enterprise import PostgresReplayStore, PostgresRevocationStore


DATABASE_URL = os.getenv("POSTGRES_ATOMIC_DATABASE_URL")
EXAMPLE_DATABASE_URL = "postgresql+psycopg://user:password@host/db"
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or DATABASE_URL == EXAMPLE_DATABASE_URL,
    reason=(
        "set POSTGRES_ATOMIC_DATABASE_URL to a real PostgreSQL URL; "
        "user:password@host/db is documentation-only placeholder text"
    ),
)


def test_postgres_claim_allows_one_mutation_for_two_independent_workers() -> None:
    prefix = f"atomic_{uuid.uuid4().hex[:16]}"
    first = PostgresAtomicOperationStore(DATABASE_URL, table_prefix=prefix)
    second = PostgresAtomicOperationStore(DATABASE_URL, table_prefix=prefix)
    metadata = MetaData()
    effects = Table(
        f"{prefix}_effects",
        metadata,
        Column("operation_id", String(255), primary_key=True),
        Column("count", Integer, nullable=False),
    )
    metadata.create_all(first.engine)
    barrier = Barrier(2)
    mutation_calls = 0
    call_lock = Lock()

    def apply(connection) -> str:
        nonlocal mutation_calls
        with call_lock:
            mutation_calls += 1
        connection.execute(insert(effects).values(operation_id="reserve-race", count=1))
        return "SUCCEEDED"

    def attempt(store: PostgresAtomicOperationStore):
        barrier.wait()
        return store.execute_once("reserve-race", apply=apply)

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, (first, second)))

        assert sorted(result.duplicate for result in results) == [False, True]
        assert {result.outcome for result in results} == {"SUCCEEDED"}
        assert mutation_calls == 1
        with first.engine.connect() as connection:
            assert connection.execute(select(effects.c.operation_id)).all() == [("reserve-race",)]
    finally:
        metadata.drop_all(first.engine)
        first.operations.drop(first.engine)
        first.outbox.drop(first.engine)


def test_postgres_authorization_replay_claim_is_atomic_across_executor_workers() -> None:
    prefix = f"auth_{uuid.uuid4().hex[:16]}"
    first = PostgresReplayStore(DATABASE_URL, table_name=f"{prefix}_replay")
    second = PostgresReplayStore(DATABASE_URL, table_name=f"{prefix}_replay")
    barrier = Barrier(2)

    def attempt(store: PostgresReplayStore) -> bool:
        barrier.wait()
        return store.claim("AUTH-RACE", 2_000_000_000)

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert sorted(workers.map(attempt, (first, second))) == [False, True]

        revocations = PostgresRevocationStore(DATABASE_URL, table_name=f"{prefix}_revocations")
        revocations.revoke("DEC-RACE", expires_at=2_000_000_000)
        assert PostgresRevocationStore(
            DATABASE_URL, table_name=f"{prefix}_revocations"
        ).is_revoked("DEC-RACE", now=1_900_000_000)
    finally:
        first.claims.drop(first.engine)
        revocations.revocations.drop(revocations.engine)


def test_postgres_event_receipt_allows_one_co_located_effect_for_two_workers() -> None:
    prefix = f"receipt_{uuid.uuid4().hex[:16]}"
    first = PostgresEventReceiptStore(DATABASE_URL, table_name=f"{prefix}_receipts")
    second = PostgresEventReceiptStore(DATABASE_URL, table_name=f"{prefix}_receipts")
    metadata = MetaData()
    effects = Table(
        f"{prefix}_effects",
        metadata,
        Column("event_id", String(255), primary_key=True),
        Column("count", Integer, nullable=False),
    )
    metadata.create_all(first.engine)
    barrier = Barrier(2)
    mutation_calls = 0
    call_lock = Lock()

    def apply(connection) -> None:
        nonlocal mutation_calls
        with call_lock:
            mutation_calls += 1
        connection.execute(insert(effects).values(event_id="event-race", count=1))

    def attempt(store: PostgresEventReceiptStore):
        barrier.wait()
        return store.apply_once("event-race", apply=apply)

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, (first, second)))

        assert sorted(result.applied for result in results) == [False, True]
        assert mutation_calls == 1
        with first.engine.connect() as connection:
            assert connection.execute(select(effects.c.event_id)).all() == [("event-race",)]
    finally:
        metadata.drop_all(first.engine)
        first.receipts.drop(first.engine)


def test_postgres_migration_upgrades_a_legacy_outbox_and_allows_ha_leasing() -> None:
    prefix = f"upgrade_{uuid.uuid4().hex[:16]}"
    state_table, receipt_table = f"{prefix}_states", f"{prefix}_receipts"
    legacy = MetaData()
    Table(
        f"{prefix}_outbox", legacy,
        Column("event_id", String(255), primary_key=True),
        Column("operation_id", String(255), nullable=False, unique=True),
        Column("event_type", String(255), nullable=False),
        Column("entity_id", String(255), nullable=False),
        Column("entity_type", String(255), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("source", String(255)),
        Column("metadata_json", Text, nullable=False),
        Column("idempotency_key", String(255)),
        Column("status", String(16), nullable=False),
        Column("published_at", DateTime(timezone=True)),
    )
    runner = PostgresMigrationRunner(DATABASE_URL, component=f"agent_postgres_{prefix}")
    legacy.create_all(runner.engine)
    atomic = None
    try:
        assert migrate_agent_postgres(
            DATABASE_URL, table_prefix=prefix, state_table=state_table, receipt_table=receipt_table,
        ) == [1, 2]
        atomic = PostgresAtomicOperationStore(DATABASE_URL, table_prefix=prefix)
        assert {"lease_owner", "lease_expires_at", "publish_attempts"} <= {
            column["name"] for column in inspect(atomic.engine).get_columns(atomic.outbox.name)
        }
        event = Event("order.updated", "ORD-UPGRADE", "order", {"status": "ready"})
        atomic.execute_once("upgrade-operation", apply=lambda _: "SUCCEEDED", outbox_event=event)
        assert [claimed.event_id for claimed in atomic.claim_outbox("migration-worker")] == [event.event_id]
    finally:
        if atomic is not None:
            atomic.operations.drop(atomic.engine)
            atomic.outbox.drop(atomic.engine)
            atomic.engine.dispose()
        else:
            # Preserve local/CI database hygiene even when the migration is
            # the assertion that failed before the current adapter existed.
            legacy.drop_all(runner.engine)
        # These tables may have been created before a migration failure.
        from sqlalchemy import create_engine
        engine = create_engine(DATABASE_URL)
        metadata = MetaData()
        metadata.reflect(engine, only=[state_table, receipt_table])
        metadata.drop_all(engine)
        with runner.engine.begin() as connection:
            connection.execute(delete(runner.versions).where(runner.versions.c.component == runner.component))
