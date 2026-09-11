from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
import inspect

import pytest

from agent_app import (
    AmbiguousCapabilityError,
    AuthorizationError,
    CapabilityInvoker,
    ConversationalGateway,
    UnknownCapabilityError,
)
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


class InMemoryStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class AvailabilityPolicy:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        if not self.allowed:
            return []
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type="AVAILABILITY_ALLOWED",
                severity="LOW",
                reason="Inventory policy allowed the request",
            )
        ]


class RecordingExecutor:
    def __init__(self, transport: InMemoryEventTransport) -> None:
        self.transport = transport
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)
        self.transport.publish(
            Event(
                event_type="inventory.availability.result",
                entity_id=action.entity_id,
                entity_type="inventory_item",
                payload={"product_id": action.entity_id, "available": True, "quantity": 17},
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

    def request_availability(self, product_id: str, correlation_id: str) -> Capability:
        return self.invoker.invoke(
            Intent("inventory.check_availability", {"product_id": product_id}),
            entity_id=product_id,
            entity_type="order_request",
            context={"correlation_id": correlation_id, "causation_id": "order-action-1"},
        )


def inventory_capability(capability_id: str = "inventory.check_availability") -> Capability:
    return Capability(
        capability_id=capability_id,
        name="Check product availability",
        description="Check inventory availability for a product.",
        metadata={
            "owner": "inventory",
            "result_event_type": "inventory.availability.result",
            "intent_types": ("inventory.check_availability",),
        },
    )


def test_agent_invokes_inventory_through_one_event_hop() -> None:
    registry = InMemoryCapabilityRegistry()
    capability = inventory_capability()
    registry.register(capability)
    transport = InMemoryEventTransport()
    invoker = CapabilityInvoker(registry, transport)
    inventory_store = InMemoryStateStore()
    inventory_policy = AvailabilityPolicy(allowed=True)
    executor = RecordingExecutor(transport)
    inventory_agent = Agent(inventory_store, inventory_policy, action_executor=executor)
    order_results: list[Event] = []

    def deliver_to_inventory(event: Event) -> None:
        result = inventory_agent.process(event)
        if result.actions:
            return
        transport.publish(
            Event(
                event_type="inventory.availability.result",
                entity_id=event.entity_id,
                entity_type="inventory_item",
                payload={"available": False},
                source="inventory",
                metadata={
                    "correlation_id": event.metadata["correlation_id"],
                    "causation_id": event.event_id,
                },
            )
        )

    transport.subscribe("capability.invocation", deliver_to_inventory)
    transport.subscribe("inventory.availability.result", order_results.append)

    discovered = OrderAgentRequester(invoker).request_availability("SKU-123", "C1")

    assert discovered == capability
    assert [event.event_type for event in transport.events] == [
        "capability.invocation",
        "inventory.availability.result",
    ]
    assert inventory_policy.calls == 1
    assert len(executor.actions) == 1
    assert order_results[0].payload["quantity"] == 17
    assert transport.events[0].metadata["owner"] == "inventory"
    assert transport.events[0].metadata["correlation_id"] == "C1"
    assert transport.events[0].metadata["causation_id"] == "order-action-1"
    assert executor.actions[0].metadata["correlation_id"] == "C1"
    assert executor.actions[0].metadata["causation_id"] == transport.events[0].event_id
    assert order_results[0].metadata["correlation_id"] == "C1"
    assert order_results[0].metadata["causation_id"] == executor.actions[0].action_id
    assert inventory_store.get("SKU-123", "order_request") is not None


def test_unknown_capability_publishes_no_event() -> None:
    registry = InMemoryCapabilityRegistry()
    transport = InMemoryEventTransport()
    invoker = CapabilityInvoker(registry, transport)

    with pytest.raises(UnknownCapabilityError):
        invoker.invoke(
            Intent("inventory.unknown"),
            entity_id="SKU-123",
            entity_type="order_request",
        )

    assert transport.events == []


def test_invoker_rejects_unauthorized_request_before_publication() -> None:
    class RoleAuthorizer:
        def authorize(self, capability: Capability, context: dict[str, Any]) -> bool:
            return "inventory:read" in context.get("roles", [])

    registry = InMemoryCapabilityRegistry()
    registry.register(inventory_capability())
    transport = InMemoryEventTransport()
    invoker = CapabilityInvoker(registry, transport, authorizer=RoleAuthorizer())

    with pytest.raises(AuthorizationError):
        invoker.invoke(
            Intent("inventory.check_availability"),
            entity_id="SKU-123",
            entity_type="order_request",
            context={"roles": []},
        )

    assert transport.events == []


def test_invoker_propagates_intent_idempotency_key_to_event() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(inventory_capability())
    transport = InMemoryEventTransport()

    CapabilityInvoker(registry, transport).invoke(
        Intent("inventory.check_availability", idempotency_key="operation-123"),
        entity_id="SKU-123",
        entity_type="order_request",
    )

    assert transport.events[0].idempotency_key == "operation-123"


def test_ambiguous_capability_publishes_no_event() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(inventory_capability("inventory.primary"))
    registry.register(inventory_capability("inventory.secondary"))
    transport = InMemoryEventTransport()
    invoker = CapabilityInvoker(registry, transport)

    with pytest.raises(AmbiguousCapabilityError):
        invoker.invoke(
            Intent("inventory.check_availability"),
            entity_id="SKU-123",
            entity_type="order_request",
        )

    assert transport.events == []


def test_target_policy_denial_returns_event_without_action() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(inventory_capability())
    transport = InMemoryEventTransport()
    invoker = CapabilityInvoker(registry, transport)
    inventory_policy = AvailabilityPolicy(allowed=False)
    executor = RecordingExecutor(transport)
    inventory_agent = Agent(InMemoryStateStore(), inventory_policy, action_executor=executor)
    results: list[Event] = []

    def deliver_denial(event: Event) -> None:
        result = inventory_agent.process(event)
        assert result.actions == []
        transport.publish(
            Event(
                event_type="inventory.availability.result",
                entity_id=event.entity_id,
                entity_type="inventory_item",
                payload={"available": False, "denied": True},
                source="inventory",
                metadata={
                    "correlation_id": event.metadata["correlation_id"],
                    "causation_id": event.event_id,
                },
            )
        )

    transport.subscribe("capability.invocation", deliver_denial)
    transport.subscribe("inventory.availability.result", results.append)
    invoker.invoke(
        Intent("inventory.check_availability"),
        entity_id="SKU-123",
        entity_type="order_request",
        context={"correlation_id": "deny-1"},
    )

    assert executor.actions == []
    assert results[0].payload["denied"] is True
    assert results[0].metadata["correlation_id"] == "deny-1"


def test_result_does_not_trigger_recursive_invocation() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(inventory_capability())
    transport = InMemoryEventTransport()
    invoker = CapabilityInvoker(registry, transport)
    invoker.invoke(
        Intent("inventory.check_availability"),
        entity_id="SKU-123",
        entity_type="order_request",
    )

    transport.publish(
        Event(
            event_type="inventory.availability.result",
            entity_id="SKU-123",
            entity_type="inventory_item",
            payload={"available": True},
            metadata={"correlation_id": "not-pending", "causation_id": "action-1"},
        )
    )

    assert [event.event_type for event in transport.events] == [
        "capability.invocation",
        "inventory.availability.result",
    ]


def test_invoker_has_no_direct_agent_dependency() -> None:
    source = inspect.getsource(CapabilityInvoker)
    assert ".process(" not in source
    assert "ConversationalGateway" not in source
    assert "agent_core.runtime.agent" not in source


def test_conversation_and_agent_invocation_share_the_same_registry() -> None:
    registry = InMemoryCapabilityRegistry()
    capability = inventory_capability()
    registry.register(capability)

    class Interpreter:
        def interpret(self, request: str, context: dict[str, Any]) -> Intent:
            return Intent("inventory.check_availability")

    class ResponseInterpreter:
        def interpret(self, event: Event, context: dict[str, Any]) -> str:
            return "available"

    transport = InMemoryEventTransport()
    conversation = ConversationalGateway(
        Interpreter(),
        registry,
        transport,
        ResponseInterpreter(),
    )
    invoker = CapabilityInvoker(registry, transport)

    assert conversation.handle(
        "Is it available?",
        entity_id="SKU-123",
        entity_type="inventory_item",
    ) == capability
    assert invoker.invoke(
        Intent("inventory.check_availability"),
        entity_id="SKU-123",
        entity_type="order_request",
    ) == capability
