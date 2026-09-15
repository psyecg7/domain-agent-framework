"""Opt-in proof that Order correlation and the outbox survive PostgreSQL restart."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from kad_inventory import (
    InMemoryInventoryState, InventoryReallocationCommandHandler,
    OrderInventoryCoordinator, PostgresOrderProcessStore,
)
from kad_inventory.contracts import KafkaMessage, MessageMetadata, MoveOrderIntent
from kad_inventory.order_protocol import OrderProcessSnapshot
from kad_inventory.events import INVENTORY_COMMANDS_TOPIC


DATABASE_URL = os.getenv("KAD_POSTGRES_DATABASE_URL") or os.getenv("POSTGRES_ATOMIC_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set KAD_POSTGRES_DATABASE_URL to run the PostgreSQL KAD protocol proof",
)


class InMemoryTopicPublisher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def publish(self, event: dict, *, topic: str | None = None, key: str | None = None) -> None:
        self.messages.append({"topic": topic, "key": key, "event": event})


def test_postgres_order_process_and_outbox_survive_reconstruction() -> None:
    prefix = f"kad_order_{uuid.uuid4().hex[:16]}"
    publisher = InMemoryTopicPublisher()
    first_store = PostgresOrderProcessStore(DATABASE_URL, table_prefix=prefix)
    first = OrderInventoryCoordinator(first_store, publisher)
    state = InMemoryInventoryState()
    state.seed("SKU-PG", "WH-B", available_quantity=1)
    try:
        pending = first.submit(MoveOrderIntent(
            order_id="ORD-PG", sku="SKU-PG", target_warehouse="WH-B", quantity=1, expected_state_version=1,
        ))
        request = next(item["event"] for item in publisher.messages if item["topic"] == INVENTORY_COMMANDS_TOPIC)
        result = InventoryReallocationCommandHandler(state, publisher).handle(request).model_dump()

        # Simulate a new worker on a separate process/connection.
        restarted = OrderInventoryCoordinator(
            PostgresOrderProcessStore(DATABASE_URL, table_prefix=prefix), publisher,
        )
        assert restarted.store.load(pending.correlation_id).status == "PENDING_INVENTORY"
        assert restarted.observe_inventory_result(result) is True
        assert restarted.observe_inventory_result(result) is False
        assert restarted.store.load(pending.correlation_id).status == "SUCCEEDED"
    finally:
        first_store.outbox.drop(first_store.engine)
        first_store.processes.drop(first_store.engine)


def test_postgres_outbox_lease_is_claimed_by_only_one_of_two_publishers() -> None:
    prefix = f"kad_lease_{uuid.uuid4().hex[:16]}"
    first = PostgresOrderProcessStore(DATABASE_URL, table_prefix=prefix)
    second = PostgresOrderProcessStore(DATABASE_URL, table_prefix=prefix)
    request = KafkaMessage(
        event_type="InventoryReallocationRequested",
        entity_id="ORD-LEASE",
        payload={"order_id": "ORD-LEASE", "sku": "SKU-LEASE", "target_warehouse": "WH-B", "quantity": 1, "expected_state_version": 1},
        metadata=MessageMetadata(
            correlation_id="order:ORD-LEASE", causation_id="order-submitted:ORD-LEASE",
            operation_id="ORD-LEASE:inventory-reallocation", attempt_id="ORD-LEASE:inventory-reallocation:attempt-1",
        ),
    )
    snapshot = OrderProcessSnapshot(
        correlation_id="order:ORD-LEASE", order_id="ORD-LEASE",
        operation_id=request.metadata.operation_id, attempt_id=request.metadata.attempt_id,
        request_event_id=request.event_id, status="PENDING_INVENTORY", applied_event_ids=(),
    )
    barrier = Barrier(2)
    try:
        first.create(snapshot, request)

        def claim(store: PostgresOrderProcessStore, worker: str) -> list[KafkaMessage]:
            barrier.wait()
            return store.claim_pending(worker)

        with ThreadPoolExecutor(max_workers=2) as workers:
            claims = list(workers.map(lambda item: claim(*item), ((first, "publisher-a"), (second, "publisher-b"))))
        assert sorted(len(claim) for claim in claims) == [0, 1]
    finally:
        first.outbox.drop(first.engine)
        first.processes.drop(first.engine)
