from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
import inspect

from agent_application import CapabilityInvoker
from agent_core import Action, Agent, Capability, Decision, Event, InMemoryCapabilityRegistry, Intent, State, decision_to_action


class DomainStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class EventTransport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Callable[[Event], Any]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], Any]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers[event.event_type]:
            handler(event)


class InventoryPolicy:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        if state.values.get("available_stock", 0) <= 0:
            return []
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type="INVENTORY_AVAILABLE",
                severity="LOW",
                reason="Inventory has stock available",
            )
        ]


class OrderPolicy:
    def evaluate(self, state: State) -> list[Decision]:
        if state.values.get("available") is True:
            return [
                Decision(
                    entity_id=state.entity_id,
                    entity_type=state.entity_type,
                    decision_type="ORDER_CAN_PROCEED",
                    severity="LOW",
                    reason="Inventory confirmed availability",
                )
            ]
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type="ORDER_BLOCKED",
                severity="MEDIUM",
                reason="Inventory denied availability",
            )
        ]


class RecordingExecutor:
    def __init__(self) -> None:
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)


class InventoryResultExecutor:
    def __init__(self, transport: EventTransport) -> None:
        self.transport = transport
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)
        self.transport.publish(
            Event(
                event_type="inventory.availability.result",
                entity_id=action.parameters["order_id"],
                entity_type="order",
                payload={
                    "product_id": action.parameters["product_id"],
                    "available": True,
                    "quantity": action.parameters["quantity"],
                },
                source="inventory",
                metadata={
                    "correlation_id": action.metadata["correlation_id"],
                    "causation_id": action.action_id,
                },
            )
        )


class OrderAgentRequester:
    def __init__(self, invoker: CapabilityInvoker) -> None:
        self.invoker = invoker

    def request_inventory(self, order_id: str, product_id: str, correlation_id: str) -> Capability:
        return self.invoker.invoke(
            Intent(
                "inventory.availability.check",
                {"order_id": order_id, "product_id": product_id},
            ),
            entity_id=product_id,
            entity_type="inventory_item",
            context={"correlation_id": correlation_id, "causation_id": f"{order_id}.request"},
        )


def make_capability(capability_id: str, owner: str, intent_type: str) -> Capability:
    return Capability(
        capability_id=capability_id,
        name=f"{owner} capability",
        description=f"A capability owned by {owner}.",
        metadata={"owner": owner, "intent_types": (intent_type,)},
    )


def build_pharmacy_application(
    available_stock: int,
) -> tuple[
    InMemoryCapabilityRegistry,
    EventTransport,
    DomainStateStore,
    DomainStateStore,
    DomainStateStore,
    DomainStateStore,
    InventoryPolicy,
    InventoryResultExecutor,
    RecordingExecutor,
    CapabilityInvoker,
]:
    registry = InMemoryCapabilityRegistry()
    registry.register(make_capability("product.information.retrieve", "pim", "product.information.retrieve"))
    registry.register(make_capability("inventory.availability.check", "inventory", "inventory.availability.check"))
    registry.register(make_capability("pricing.price.retrieve", "pricing", "pricing.price.retrieve"))
    registry.register(make_capability("order.create", "order", "order.create"))
    registry.register(make_capability("supplier.stock.replenish", "supplier", "supplier.stock.replenish"))

    transport = EventTransport()
    pim_store = DomainStateStore()
    inventory_store = DomainStateStore()
    pricing_store = DomainStateStore()
    order_store = DomainStateStore()
    inventory_store.save(
        State(
            entity_id="P123",
            entity_type="inventory_item",
            values={"available_stock": available_stock},
        )
    )
    pim_store.save(
        State(
            entity_id="P123",
            entity_type="product",
            values={"name": "Pharmacy product", "status": "ACTIVE"},
        )
    )
    pricing_store.save(
        State(entity_id="P123", entity_type="price", values={"amount": 12.50, "currency": "EUR"})
    )

    inventory_policy = InventoryPolicy()
    inventory_executor = InventoryResultExecutor(transport)
    inventory_agent = Agent(
        inventory_store,
        inventory_policy,
        action_executor=inventory_executor,
        action_factory=lambda decision: decision_to_action(
            decision,
            "REPORT_AVAILABILITY",
            parameters={
                "order_id": "ORD-1",
                "product_id": decision.entity_id,
                "quantity": inventory_store.states[decision.entity_id, decision.entity_type].values[
                    "available_stock"
                ],
            },
        ),
    )
    order_executor = RecordingExecutor()
    order_agent = Agent(order_store, OrderPolicy(), action_executor=order_executor)

    def route_inventory(event: Event) -> None:
        result = inventory_agent.process(event)
        if result.actions:
            return
        transport.publish(
            Event(
                event_type="inventory.availability.result",
                entity_id=event.payload["intent"]["parameters"]["order_id"],
                entity_type="order",
                payload={
                    "product_id": event.payload["intent"]["parameters"]["product_id"],
                    "available": False,
                    "quantity": 0,
                },
                source="inventory",
                metadata={
                    "correlation_id": event.metadata["correlation_id"],
                    "causation_id": event.event_id,
                },
            )
        )

    transport.subscribe("capability.invocation", route_inventory)
    transport.subscribe("inventory.availability.result", order_agent.process)
    invoker = CapabilityInvoker(registry, transport)
    return (
        registry,
        transport,
        pim_store,
        inventory_store,
        pricing_store,
        order_store,
        inventory_policy,
        inventory_executor,
        order_executor,
        invoker,
    )


def test_pharmacy_domains_keep_ownership_and_order_requests_inventory() -> None:
    (
        registry,
        transport,
        pim_store,
        inventory_store,
        pricing_store,
        order_store,
        inventory_policy,
        inventory_executor,
        order_executor,
        invoker,
    ) = build_pharmacy_application(available_stock=17)

    requester = OrderAgentRequester(invoker)
    capability = requester.request_inventory("ORD-1", "P123", "pharmacy-flow-1")

    assert capability.metadata["owner"] == "inventory"
    assert registry.get("product.information.retrieve").metadata["owner"] == "pim"
    assert registry.get("pricing.price.retrieve").metadata["owner"] == "pricing"
    assert registry.get("order.create").metadata["owner"] == "order"
    assert [event.event_type for event in transport.events] == [
        "capability.invocation",
        "inventory.availability.result",
    ]
    assert inventory_policy.calls == 1
    assert inventory_executor.actions[0].action_type == "REPORT_AVAILABILITY"
    assert order_executor.actions[0].action_type == "ORDER_CAN_PROCEED"
    assert order_store.get("ORD-1", "order") is not None
    product_state = pim_store.get("P123", "product")
    price_state = pricing_store.get("P123", "price")
    inventory_state = inventory_store.get("P123", "inventory_item")
    assert product_state is not None
    assert price_state is not None
    assert inventory_state is not None
    assert product_state.values["name"] == "Pharmacy product"
    assert price_state.values["amount"] == 12.50
    assert inventory_state.values["available_stock"] == 17

    invocation, result = transport.events
    assert invocation.metadata["correlation_id"] == "pharmacy-flow-1"
    assert result.metadata["correlation_id"] == "pharmacy-flow-1"
    assert invocation.metadata["causation_id"] == "ORD-1.request"
    assert inventory_executor.actions[0].metadata["causation_id"] == invocation.event_id
    assert result.metadata["causation_id"] == inventory_executor.actions[0].action_id


def test_inventory_denial_is_authoritative_and_creates_no_false_availability_action() -> None:
    (
        _registry,
        transport,
        _pim_store,
        _inventory_store,
        _pricing_store,
        _order_store,
        inventory_policy,
        inventory_executor,
        order_executor,
        invoker,
    ) = build_pharmacy_application(available_stock=0)

    OrderAgentRequester(invoker).request_inventory("ORD-1", "P123", "pharmacy-flow-2")

    assert inventory_policy.calls == 1
    assert inventory_executor.actions == []
    assert order_executor.actions[0].action_type == "ORDER_BLOCKED"
    assert transport.events[-1].payload == {
        "product_id": "P123",
        "available": False,
        "quantity": 0,
    }


def test_pharmacy_flow_does_not_automatically_chain_supplier_capability() -> None:
    _registry, transport, *_rest = build_pharmacy_application(available_stock=0)
    invoker = _rest[-1]

    OrderAgentRequester(invoker).request_inventory("ORD-1", "P123", "pharmacy-flow-3")

    assert all(event.event_type != "supplier.stock.replenish" for event in transport.events)
    assert all(event.metadata.get("capability_id") != "supplier.stock.replenish" for event in transport.events)


def test_order_requester_has_no_inventory_agent_or_state_dependency() -> None:
    source = inspect.getsource(OrderAgentRequester)
    assert "Inventory" not in source
    assert "StateStore" not in source
    assert ".process(" not in source
