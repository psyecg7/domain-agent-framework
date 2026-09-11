from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from agent_app import ConversationalGateway, DeterministicResponseInterpreter
from agent_core import (
    Action,
    Agent,
    Capability,
    Decision,
    Event,
    InMemoryCapabilityRegistry,
    Intent,
    State,
)

class InMemoryEventTransport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Callable[[Event], Any]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], Any]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers[event.event_type]:
            handler(event)


class RequestInterpreter:
    def interpret(self, request: str, context: dict[str, Any]) -> Intent:
        return Intent(context["intent_type"], context.get("parameters", {}))


class InMemoryStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class AllowPolicy:
    def evaluate(self, state: State) -> list:
        return [] if state.values.get("deny") else [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type="CHECK_ALLOWED",
                severity="LOW",
                reason="Request allowed",
            )
        ]


class DenyPolicy:
    def evaluate(self, state: State) -> list:
        return []


class ResultExecutor:
    def __init__(self, transport: InMemoryEventTransport, result_type: str, payload: dict[str, Any]) -> None:
        self.transport = transport
        self.result_type = result_type
        self.payload = payload
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)
        self.transport.publish(
            Event(
                event_type=self.result_type,
                entity_id=action.entity_id,
                entity_type="result",
                payload=self.payload,
                source="domain-agent",
                metadata={
                    "correlation_id": action.metadata["correlation_id"],
                    "causation_id": action.action_id,
                },
            )
        )


def make_gateway(
    transport: InMemoryEventTransport,
    formatters: dict[str, Callable[[Event, dict[str, Any]], str]],
) -> ConversationalGateway:
    registry = InMemoryCapabilityRegistry()
    registry.register(
        Capability(
            capability_id="inventory.check_availability",
            name="Check inventory availability",
            description="Check current inventory availability.",
            metadata={"result_event_type": "inventory.availability.result"},
        )
    )
    registry.register(
        Capability(
            capability_id="order.get_status",
            name="Get order status",
            description="Get the current order status.",
            metadata={"result_event_type": "order.status.result"},
        )
    )
    return ConversationalGateway(
        RequestInterpreter(),
        registry,
        transport,
        DeterministicResponseInterpreter(formatters),
    )


def test_complete_inventory_request_and_result_loop() -> None:
    transport = InMemoryEventTransport()
    gateway = make_gateway(
        transport,
        {
            "inventory.availability.result": lambda event, context: (
                f"{event.payload['product_id']} is available. "
                f"Current quantity: {event.payload['quantity']}."
            )
        },
    )
    executor = ResultExecutor(
        transport,
        "inventory.availability.result",
        {"product_id": "SKU-123", "available": True, "quantity": 17},
    )
    agent = Agent(InMemoryStateStore(), AllowPolicy(), action_executor=executor)
    transport.subscribe("intent.requested", agent.process)

    gateway.handle(
        "Is SKU-123 available?",
        entity_id="SKU-123",
        entity_type="inventory_item",
        context={
            "intent_type": "inventory.check_availability",
            "parameters": {"product_id": "SKU-123"},
            "correlation_id": "C1",
            "request_id": "request-1",
        },
    )

    assert gateway.responses[0].text == "SKU-123 is available. Current quantity: 17."
    assert gateway.responses[0].correlation_id == "C1"
    assert gateway.responses[0].causation_id == executor.actions[0].action_id
    assert executor.actions[0].metadata["correlation_id"] == "C1"
    assert executor.actions[0].metadata["causation_id"] == transport.events[0].event_id


def test_one_gateway_handles_two_domain_result_types() -> None:
    transport = InMemoryEventTransport()
    gateway = make_gateway(
        transport,
        {
            "inventory.availability.result": lambda event, context: "inventory response",
            "order.status.result": lambda event, context: "order response",
        },
    )

    gateway.handle(
        "check inventory",
        entity_id="SKU-123",
        entity_type="inventory_item",
        context={"intent_type": "inventory.check_availability", "correlation_id": "inventory-1"},
    )
    gateway.handle(
        "check order",
        entity_id="ORD-9",
        entity_type="order",
        context={"intent_type": "order.get_status", "correlation_id": "order-1"},
    )

    transport.publish(
        Event(
            event_type="order.status.result",
            entity_id="ORD-9",
            entity_type="order",
            payload={"status": "SHIPPED"},
            metadata={"correlation_id": "order-1", "causation_id": "order-action"},
        )
    )
    transport.publish(
        Event(
            event_type="inventory.availability.result",
            entity_id="SKU-123",
            entity_type="inventory_item",
            payload={"available": True},
            metadata={"correlation_id": "inventory-1", "causation_id": "inventory-action"},
        )
    )

    assert [response.text for response in gateway.responses] == ["order response", "inventory response"]
    assert [response.correlation_id for response in gateway.responses] == ["order-1", "inventory-1"]


def test_gateway_generates_unique_correlation_ids_when_omitted() -> None:
    transport = InMemoryEventTransport()
    gateway = make_gateway(transport, {})

    gateway.handle(
        "check inventory",
        entity_id="SKU-123",
        entity_type="inventory_item",
        context={"intent_type": "inventory.check_availability"},
    )
    gateway.handle(
        "check inventory again",
        entity_id="SKU-123",
        entity_type="inventory_item",
        context={"intent_type": "inventory.check_availability"},
    )

    correlation_ids = [event.metadata["correlation_id"] for event in transport.events]
    assert len(correlation_ids) == 2
    assert len(set(correlation_ids)) == 2


def test_unknown_and_malformed_results_are_ignored() -> None:
    transport = InMemoryEventTransport()
    gateway = make_gateway(
        transport,
        {"inventory.availability.result": lambda event, context: "valid response"},
    )
    gateway.handle(
        "check inventory",
        entity_id="SKU-123",
        entity_type="inventory_item",
        context={"intent_type": "inventory.check_availability", "correlation_id": "C1"},
    )

    malformed_events = [
        Event("", "SKU-123", "inventory_item", {}, metadata={"correlation_id": "C1", "causation_id": "a"}),
        Event("inventory.availability.result", "SKU-123", "inventory_item", [], metadata={"correlation_id": "C1", "causation_id": "a"}),
        Event("inventory.availability.result", "SKU-123", "inventory_item", {}, metadata={"causation_id": "a"}),
        Event("inventory.availability.result", "SKU-123", "inventory_item", {}, metadata={"correlation_id": "unknown", "causation_id": "a"}),
        Event("unknown.result", "SKU-123", "inventory_item", {}, metadata={"correlation_id": "C1", "causation_id": "a"}),
    ]

    for event in malformed_events:
        assert gateway.handle_result(event) is None

    assert gateway.responses == []


def test_policy_denial_is_rendered_without_creating_an_action() -> None:
    transport = InMemoryEventTransport()
    gateway = make_gateway(
        transport,
        {
            "inventory.availability.result": lambda event, context: (
                "I can't complete that request because the domain policy denied it."
                if not event.payload["allowed"]
                else "allowed"
            )
        },
    )
    executor = ResultExecutor(transport, "inventory.availability.result", {"allowed": True})
    agent = Agent(InMemoryStateStore(), DenyPolicy(), action_executor=executor)
    transport.subscribe("intent.requested", agent.process)

    gateway.handle(
        "check inventory",
        entity_id="SKU-123",
        entity_type="inventory_item",
        context={"intent_type": "inventory.check_availability", "correlation_id": "deny-1"},
    )
    assert executor.actions == []

    denial = Event(
        event_type="inventory.availability.result",
        entity_id="SKU-123",
        entity_type="inventory_item",
        payload={"allowed": False},
        metadata={"correlation_id": "deny-1", "causation_id": "policy-denial"},
    )
    transport.publish(denial)

    assert gateway.responses[-1].text == "I can't complete that request because the domain policy denied it."


def test_gateway_has_no_domain_agent_dependency() -> None:
    import inspect

    source = inspect.getsource(ConversationalGateway)
    assert "Agent" not in source
    assert "StateStore" not in source
    assert "ActionExecutor" not in source
