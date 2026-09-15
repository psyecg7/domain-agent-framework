from __future__ import annotations

from kad_inventory import (
    InMemoryInventoryState, InventoryReallocationCommandHandler,
    OrderInventoryCoordinator, SqliteOrderProcessStore,
)
from kad_inventory.contracts import MoveOrderIntent
from kad_inventory.events import INVENTORY_COMMANDS_TOPIC, ORDER_RESULTS_TOPIC


class InMemoryTopicPublisher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def publish(self, event: dict, *, topic: str | None = None, key: str | None = None) -> None:
        self.messages.append({"topic": topic, "key": key, "event": event})


def test_interleaved_orders_are_correlated_after_result_delivery_in_reverse_order(tmp_path) -> None:
    state = InMemoryInventoryState()
    state.seed("SKU-A", "WH-B", available_quantity=1)
    state.seed("SKU-B", "WH-B", available_quantity=1)
    publisher = InMemoryTopicPublisher()
    coordinator = OrderInventoryCoordinator(SqliteOrderProcessStore(tmp_path / "orders.sqlite"), publisher)
    inventory = InventoryReallocationCommandHandler(state, publisher)

    first = coordinator.submit(MoveOrderIntent(
        order_id="ORD-A", sku="SKU-A", target_warehouse="WH-B", quantity=1, expected_state_version=1,
    ))
    second = coordinator.submit(MoveOrderIntent(
        order_id="ORD-B", sku="SKU-B", target_warehouse="WH-B", quantity=1, expected_state_version=1,
    ))
    requests = [message for message in publisher.messages if message["topic"] == INVENTORY_COMMANDS_TOPIC]
    assert [message["key"] for message in requests] == ["SKU-A|WH-B", "SKU-B|WH-B"]

    inventory.handle(requests[0]["event"])
    inventory.handle(requests[1]["event"])
    results = [message for message in publisher.messages if message["topic"] == ORDER_RESULTS_TOPIC]
    assert [message["key"] for message in results] == ["ORD-A", "ORD-B"]

    # Results can be delivered in a different order from the requests. Their
    # durable correlation identity—not the topic position—selects the process.
    assert coordinator.observe_inventory_result(results[1]["event"]) is True
    assert coordinator.observe_inventory_result(results[0]["event"]) is True
    assert coordinator.store.load(first.correlation_id).status == "SUCCEEDED"
    assert coordinator.store.load(second.correlation_id).status == "SUCCEEDED"


def test_result_is_applied_after_restart_once_and_rejects_a_stale_attempt(tmp_path) -> None:
    state = InMemoryInventoryState()
    state.seed("SKU-992", "WH-B", available_quantity=40)
    publisher = InMemoryTopicPublisher()
    database = tmp_path / "orders.sqlite"
    first_runtime = OrderInventoryCoordinator(SqliteOrderProcessStore(database), publisher)
    pending = first_runtime.submit(MoveOrderIntent(
        order_id="ORD-992", sku="SKU-992", target_warehouse="WH-B", quantity=40, expected_state_version=1,
    ))
    request = next(message["event"] for message in publisher.messages if message["topic"] == INVENTORY_COMMANDS_TOPIC)
    result = InventoryReallocationCommandHandler(state, publisher).handle(request).model_dump()

    # A replacement process after a crash recovers only durable correlation state.
    restarted = OrderInventoryCoordinator(SqliteOrderProcessStore(database), publisher)
    assert restarted.store.load(pending.correlation_id).status == "PENDING_INVENTORY"
    assert restarted.observe_inventory_result(result) is True
    assert restarted.observe_inventory_result(result) is False  # broker redelivery
    assert restarted.store.load(pending.correlation_id).status == "SUCCEEDED"

    stale = {**result, "event_id": "late-result", "metadata": {**result["metadata"], "attempt_id": "old-attempt"}}
    try:
        restarted.observe_inventory_result(stale)
    except ValueError as exc:
        assert "current Order operation attempt" in str(exc)
    else:
        raise AssertionError("a stale result attempt was accepted")


def test_stale_inventory_command_returns_to_the_matching_order_as_conflict(tmp_path) -> None:
    state = InMemoryInventoryState()
    state.seed("SKU-STALE", "WH-B", available_quantity=40, version_token=1)
    publisher = InMemoryTopicPublisher()
    coordinator = OrderInventoryCoordinator(SqliteOrderProcessStore(tmp_path / "orders.sqlite"), publisher)
    pending = coordinator.submit(MoveOrderIntent(
        order_id="ORD-STALE", sku="SKU-STALE", target_warehouse="WH-B", quantity=40, expected_state_version=1,
    ))
    request = next(message["event"] for message in publisher.messages if message["topic"] == INVENTORY_COMMANDS_TOPIC)

    # The authoritative Inventory stream changes while the command waits in Kafka.
    state.apply_operational_event({
        "event_type": "CustomerInventoryConsumed", "sku": "SKU-STALE", "warehouse": "WH-B",
        "available_quantity": 0, "version_token": 2,
    })
    result = InventoryReallocationCommandHandler(state, publisher).handle(request).model_dump()

    assert result["event_type"] == "ReallocationFailed"
    assert result["metadata"]["correlation_id"] == pending.correlation_id
    assert coordinator.observe_inventory_result(result) is True
    assert coordinator.store.load(pending.correlation_id).status == "CONFLICT"
    assert state.snapshot("SKU-STALE", "WH-B").available_quantity == 0


def test_duplicate_order_submission_publishes_only_one_stable_inventory_request(tmp_path) -> None:
    state = InMemoryInventoryState()
    state.seed("SKU-IDEMPOTENT", "WH-B", available_quantity=1)
    publisher = InMemoryTopicPublisher()
    coordinator = OrderInventoryCoordinator(SqliteOrderProcessStore(tmp_path / "orders.sqlite"), publisher)
    intent = MoveOrderIntent(
        order_id="ORD-IDEMPOTENT", sku="SKU-IDEMPOTENT", target_warehouse="WH-B", quantity=1, expected_state_version=1,
    )

    first = coordinator.submit(intent)
    duplicate = coordinator.submit(intent)

    assert duplicate == first
    requests = [message for message in publisher.messages if message["topic"] == INVENTORY_COMMANDS_TOPIC]
    assert len(requests) == 1
