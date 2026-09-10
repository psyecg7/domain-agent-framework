from __future__ import annotations

from collections import defaultdict
from typing import Callable

from agent_core import Action, Agent, Decision, Event, State, decision_to_action


class InMemoryStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class InMemoryEventTransport:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self.events: list[Event] = []

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers[event.event_type]:
            handler(event)


class EventPublishingExecutor:
    def __init__(self, transport: InMemoryEventTransport, event_factory: Callable[[Action], Event]) -> None:
        self.transport = transport
        self.event_factory = event_factory
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)
        self.transport.publish(self.event_factory(action))


class InventoryPolicy:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        if state.values.get("available_stock", 0) < state.values.get("reorder_point", 0):
            return [
                Decision(
                    entity_id=state.entity_id,
                    entity_type=state.entity_type,
                    decision_type="REPLENISHMENT_REQUIRED",
                    severity="MEDIUM",
                    reason="Stock is below the reorder point",
                )
            ]
        return []


class OrderingPolicy:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        if state.values.get("requested_quantity", 0) > 0:
            return [
                Decision(
                    entity_id=state.entity_id,
                    entity_type=state.entity_type,
                    decision_type="PURCHASE_ORDER_REQUIRED",
                    severity="LOW",
                    reason="Replenishment request received",
                )
            ]
        return []


def test_independent_agents_compose_through_events() -> None:
    transport = InMemoryEventTransport()
    inventory_store = InMemoryStateStore()
    ordering_store = InMemoryStateStore()
    inventory_policy = InventoryPolicy()
    ordering_policy = OrderingPolicy()
    inventory_results = []
    ordering_results = []

    inventory_agent = Agent(
        inventory_store,
        inventory_policy,
        action_executor=EventPublishingExecutor(
            transport,
            lambda action: Event(
                event_type="replenishment.requested",
                entity_id=action.entity_id,
                entity_type="replenishment_request",
                payload={"requested_quantity": action.parameters["requested_quantity"]},
                source="inventory",
                metadata={
                    "correlation_id": "flow-1",
                    "causation_id": action.action_id,
                },
            ),
        ),
        action_factory=lambda decision: decision_to_action(
            decision,
            "EMIT_REPLENISHMENT_REQUEST",
            parameters={"requested_quantity": 10},
        ),
    )

    ordering_agent = Agent(
        ordering_store,
        ordering_policy,
        action_executor=EventPublishingExecutor(
            transport,
            lambda action: Event(
                event_type="purchase_order.requested",
                entity_id=action.entity_id,
                entity_type="purchase_order_request",
                payload={"action_id": action.action_id},
                source="ordering",
                metadata={"correlation_id": "flow-1", "causation_id": action.action_id},
            ),
        ),
    )

    transport.subscribe("replenishment.requested", lambda event: ordering_results.append(ordering_agent.process(event)))

    initial_event = Event(
        event_type="stock.changed",
        entity_id="sku-1",
        entity_type="inventory_item",
        payload={"available_stock": 2, "reorder_point": 5},
        source="warehouse",
        metadata={"correlation_id": "flow-1"},
    )
    inventory_results.append(inventory_agent.process(initial_event))

    assert [event.event_type for event in transport.events] == [
        "replenishment.requested",
        "purchase_order.requested",
    ]
    assert inventory_results[0].decisions[0].decision_type == "REPLENISHMENT_REQUIRED"
    assert ordering_results[0].decisions[0].decision_type == "PURCHASE_ORDER_REQUIRED"
    assert inventory_policy.calls == 1
    assert ordering_policy.calls == 1
    assert inventory_results[0].decisions[0] is not ordering_results[0].decisions[0]

    assert inventory_store.get("sku-1", "inventory_item") is not None
    assert ordering_store.get("sku-1", "replenishment_request") is not None
    assert ordering_store.get("sku-1", "inventory_item") is None
    assert ordering_results[0].event.metadata["correlation_id"] == "flow-1"
    assert ordering_results[0].event.metadata["causation_id"] == transport.events[0].metadata["causation_id"]


def test_ordering_agent_is_not_invoked_until_transport_delivers_event() -> None:
    transport = InMemoryEventTransport()
    ordering_store = InMemoryStateStore()
    ordering_policy = OrderingPolicy()
    ordering_agent = Agent(ordering_store, ordering_policy)

    assert ordering_policy.calls == 0
    transport.subscribe("replenishment.requested", ordering_agent.process)
    assert ordering_policy.calls == 0

    transport.publish(
        Event(
            event_type="replenishment.requested",
            entity_id="sku-2",
            entity_type="replenishment_request",
            payload={"requested_quantity": 4},
        )
    )

    assert ordering_policy.calls == 1
