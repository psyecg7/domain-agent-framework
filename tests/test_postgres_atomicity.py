"""Reference checks for the optional Postgres atomic-operation adapter.

SQLite is used only to exercise transaction shape deterministically. The
cross-worker guarantee itself depends on PostgreSQL's real unique constraints
and transaction isolation, and is covered by deployment conformance testing.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select

import pytest

from agent_core import Event
from agent_postgres import OperationIdentityMismatch, PostgresAtomicOperationStore


def make_store(tmp_path):
    store = PostgresAtomicOperationStore(f"sqlite:///{tmp_path / 'atomic.db'}")
    metadata = MetaData()
    effects = Table(
        "domain_effects",
        metadata,
        Column("operation_id", String(255), primary_key=True),
        Column("value", Integer, nullable=False),
    )
    metadata.create_all(store.engine)
    return store, effects


def result_event(operation_id: str, *, event_id: str = "event-1") -> Event:
    return Event(
        event_type="inventory.reservation.result",
        entity_id="SKU-1",
        entity_type="inventory",
        payload={"operation_id": operation_id, "status": "SUCCEEDED"},
        source="inventory",
        event_id=event_id,
        metadata={"correlation_id": "correlation-1", "causation_id": operation_id},
        idempotency_key=operation_id,
    )


def effect_count(store: PostgresAtomicOperationStore, effects: Table) -> int:
    with store.engine.connect() as connection:
        return len(connection.execute(select(effects.c.operation_id)).all())


def test_atomic_operation_records_one_effect_and_one_pending_outbox_message(tmp_path) -> None:
    store, effects = make_store(tmp_path)
    event = result_event("reserve-1")
    calls = 0

    def reserve(connection) -> str:
        nonlocal calls
        calls += 1
        connection.execute(insert(effects).values(operation_id="reserve-1", value=1))
        return "SUCCEEDED"

    first = store.execute_once("reserve-1", apply=reserve, outbox_event=event)
    duplicate = store.execute_once("reserve-1", apply=reserve, outbox_event=event)

    assert first.outcome == "SUCCEEDED"
    assert not first.duplicate
    assert duplicate.outcome == "SUCCEEDED"
    assert duplicate.duplicate
    assert calls == 1
    assert effect_count(store, effects) == 1
    assert store.pending_outbox() == [event]
    assert store.mark_published(event.event_id)
    assert not store.mark_published(event.event_id)
    assert store.pending_outbox() == []


def test_failure_rolls_back_claim_domain_write_and_outbox_record(tmp_path) -> None:
    store, effects = make_store(tmp_path)

    def fail_after_mutation(connection) -> str:
        connection.execute(insert(effects).values(operation_id="reserve-rollback", value=1))
        raise RuntimeError("simulated domain failure")

    with pytest.raises(RuntimeError, match="simulated domain failure"):
        store.execute_once(
            "reserve-rollback",
            apply=fail_after_mutation,
            outbox_event=result_event("reserve-rollback", event_id="event-rollback"),
        )

    assert effect_count(store, effects) == 0
    assert store.pending_outbox() == []

    def retry(connection) -> str:
        connection.execute(insert(effects).values(operation_id="reserve-rollback", value=1))
        return "SUCCEEDED"

    retried = store.execute_once(
        "reserve-rollback",
        apply=retry,
        outbox_event=result_event("reserve-rollback", event_id="event-rollback"),
    )
    assert retried.outcome == "SUCCEEDED"
    assert not retried.duplicate
    assert effect_count(store, effects) == 1


def test_domain_conflict_is_durable_without_a_business_mutation(tmp_path) -> None:
    store, effects = make_store(tmp_path)

    result = store.execute_once(
        "reserve-stale",
        apply=lambda connection: "CONFLICT",
        outbox_event=result_event("reserve-stale", event_id="event-conflict"),
    )

    assert result.outcome == "CONFLICT"
    assert effect_count(store, effects) == 0
    assert [event.event_id for event in store.pending_outbox()] == ["event-conflict"]


def test_outbox_leases_are_owned_by_one_publisher_and_can_be_released(tmp_path) -> None:
    store, _ = make_store(tmp_path)
    event = result_event("reserve-lease", event_id="event-lease")
    store.execute_once("reserve-lease", apply=lambda connection: "SUCCEEDED", outbox_event=event)

    assert store.claim_outbox("publisher-a") == [event]
    assert store.claim_outbox("publisher-b") == []
    assert not store.mark_published(event.event_id, worker_id="publisher-b")
    assert store.release_outbox_lease(event.event_id, worker_id="publisher-a")
    assert store.claim_outbox("publisher-b") == [event]
    assert store.mark_published(event.event_id, worker_id="publisher-b")
    assert store.pending_outbox() == []


def test_duplicate_operation_cannot_replace_its_outbound_identity(tmp_path) -> None:
    store, effects = make_store(tmp_path)

    def reserve(connection) -> str:
        connection.execute(insert(effects).values(operation_id="reserve-identity", value=1))
        return "SUCCEEDED"

    store.execute_once(
        "reserve-identity",
        apply=reserve,
        outbox_event=result_event("reserve-identity", event_id="event-original"),
    )

    with pytest.raises(OperationIdentityMismatch):
        store.execute_once(
            "reserve-identity",
            apply=reserve,
            outbox_event=result_event("reserve-identity", event_id="event-replacement"),
        )
