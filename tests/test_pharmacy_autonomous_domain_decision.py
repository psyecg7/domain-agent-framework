from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
import inspect

import pytest

from agent_app import AmbiguousCapabilityError, CapabilityInvoker, UnknownCapabilityError
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent, Recommendation


class Transport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers[event.event_type]:
            handler(event)


class OrderAutonomousDecision:
    """Order business behavior, not a reusable planner or workflow runtime."""

    requirements = {
        "product_validity": "product.information.retrieve",
        "availability": "inventory.availability.check",
        "price": "pricing.price.retrieve",
    }

    def __init__(self, registry: InMemoryCapabilityRegistry, transport: Transport) -> None:
        self.registry = registry
        self.transport = transport
        self.invoker = CapabilityInvoker(registry, transport)
        self.facts: dict[str, dict[str, Any]] = defaultdict(dict)
        self.status: dict[str, str] = {}
        for event_type in ("product.result", "inventory.result", "pricing.result"):
            transport.subscribe(event_type, self._observe)

    def discover_requirements(self, request: Intent) -> dict[str, Capability]:
        if request.intent_type != "order.can_purchase":
            raise ValueError("Unsupported Order business request")
        discovered: dict[str, Capability] = {}
        for requirement, intent_type in self.requirements.items():
            matches = list(self.registry.find(Intent(intent_type, request.parameters)))
            if len(matches) == 0:
                raise UnknownCapabilityError(f"No capability for {requirement}")
            if len(matches) > 1:
                raise AmbiguousCapabilityError(f"Ambiguous capability for {requirement}")
            discovered[requirement] = matches[0]
        return discovered

    def assess(self, request: Intent, *, correlation_id: str, causation_id: str) -> dict[str, Capability]:
        discovered = self.discover_requirements(request)
        for capability in discovered.values():
            self.invoker.invoke(
                Intent(capability.capability_id, request.parameters),
                entity_id=request.parameters["product_id"],
                entity_type="purchase_request",
                context={"correlation_id": correlation_id, "causation_id": causation_id},
            )
        self.status[correlation_id] = "WAITING_FOR_FACTS"
        return discovered

    def _observe(self, event: Event) -> None:
        correlation_id = event.metadata.get("correlation_id")
        requirement = event.payload.get("requirement")
        if not isinstance(correlation_id, str) or not isinstance(requirement, str):
            return
        self.facts[correlation_id][requirement] = event.payload
        if len(self.facts[correlation_id]) < len(self.requirements):
            return
        self.status[correlation_id] = (
            "CAN_PURCHASE"
            if all(fact.get("authoritative") is True and fact.get("valid") is True for fact in self.facts[correlation_id].values())
            else "CANNOT_PURCHASE"
        )

    def validate_recommendation(self, recommendation: Recommendation) -> tuple[str, ...]:
        proposed = tuple(recommendation.parameters.get("required_capabilities", ()))
        allowed = tuple(self.requirements.values())
        if not proposed or any(capability not in allowed for capability in proposed):
            return ()
        return proposed


def capability(capability_id: str, owner: str, intent_types: tuple[str, ...] | None = None) -> Capability:
    return Capability(
        capability_id,
        capability_id,
        f"{owner} owned capability",
        metadata={"owner": owner, "intent_types": intent_types or (capability_id,)},
    )


def build_order(*, include_inventory: bool = True, ambiguous_inventory: bool = False) -> tuple[OrderAutonomousDecision, Transport, InMemoryCapabilityRegistry]:
    registry = InMemoryCapabilityRegistry()
    registry.register(capability("product.information.retrieve", "pim"))
    if include_inventory:
        registry.register(capability("inventory.availability.check", "inventory"))
    if ambiguous_inventory:
        registry.register(capability("inventory.availability.customer", "inventory", ("inventory.availability.check",)))
    registry.register(capability("pricing.price.retrieve", "pricing"))
    transport = Transport()
    return OrderAutonomousDecision(registry, transport), transport, registry


def request() -> Intent:
    return Intent("order.can_purchase", {"customer_id": "C-1", "product_id": "P123"})


def publish_fact(transport: Transport, requirement: str, valid: bool, correlation_id: str = "C1") -> None:
    transport.publish(
        Event(
            f"{requirement}.result",
            "P123",
            requirement,
            {"requirement": requirement, "valid": valid, "authoritative": True},
            source=requirement,
            metadata={"correlation_id": correlation_id, "causation_id": f"{requirement}-action"},
        )
    )


def test_order_autonomously_discovers_owned_capabilities_without_side_effects() -> None:
    order, transport, _ = build_order()

    discovered = order.discover_requirements(request())

    assert set(discovered) == {"product_validity", "availability", "price"}
    assert discovered["product_validity"].metadata["owner"] == "pim"
    assert discovered["availability"].metadata["owner"] == "inventory"
    assert discovered["price"].metadata["owner"] == "pricing"
    assert transport.events == []


def test_order_invokes_discovered_capabilities_through_events() -> None:
    order, transport, _ = build_order()

    order.assess(request(), correlation_id="C1", causation_id="order-request")

    assert [event.event_type for event in transport.events] == [
        "capability.invocation",
        "capability.invocation",
        "capability.invocation",
    ]
    assert all(event.metadata["correlation_id"] == "C1" for event in transport.events)
    assert all(event.metadata["causation_id"] == "order-request" for event in transport.events)


def test_out_of_order_authoritative_results_produce_same_order_decision() -> None:
    order, transport, _ = build_order()
    order.assess(request(), correlation_id="C1", causation_id="order-request")

    publish_fact(transport, "pricing", True)
    publish_fact(transport, "inventory", True)
    publish_fact(transport, "product", True)

    assert order.status["C1"] == "CAN_PURCHASE"


def test_inventory_denial_remains_authoritative() -> None:
    order, transport, _ = build_order()
    order.assess(request(), correlation_id="C1", causation_id="order-request")
    publish_fact(transport, "product", True)
    publish_fact(transport, "pricing", True)
    publish_fact(transport, "inventory", False)

    assert order.status["C1"] == "CANNOT_PURCHASE"
    assert order.facts["C1"]["inventory"]["authoritative"] is True


def test_missing_capability_is_unresolved_not_a_substitute_fact() -> None:
    order, transport, _ = build_order(include_inventory=False)

    with pytest.raises(UnknownCapabilityError):
        order.assess(request(), correlation_id="C1", causation_id="order-request")

    assert transport.events == []
    assert order.status.get("C1") is None


def test_ambiguous_capability_is_not_arbitrarily_selected() -> None:
    order, transport, _ = build_order(ambiguous_inventory=True)

    with pytest.raises(AmbiguousCapabilityError):
        order.assess(request(), correlation_id="C1", causation_id="order-request")

    assert transport.events == []


def test_unknown_result_does_not_become_unavailable() -> None:
    order, transport, _ = build_order()
    order.assess(request(), correlation_id="C1", causation_id="order-request")
    publish_fact(transport, "product", True)
    publish_fact(transport, "pricing", True)

    assert order.status["C1"] == "WAITING_FOR_FACTS"
    assert order.status["C1"] != "CANNOT_PURCHASE"


def test_recommendation_is_validated_before_capability_invocation() -> None:
    order, transport, _ = build_order()
    recommendation = Recommendation(
        "REQUIRED_CAPABILITIES",
        rationale="The request needs product, stock, and price facts",
        parameters={"required_capabilities": [
            "product.information.retrieve",
            "inventory.availability.check",
            "pricing.price.retrieve",
        ]},
    )

    assert order.validate_recommendation(recommendation) == tuple(order.requirements.values())
    assert transport.events == []


def test_invalid_recommendation_cannot_invoke_arbitrary_capability() -> None:
    order, transport, _ = build_order()
    recommendation = Recommendation(
        "REQUIRED_CAPABILITIES",
        parameters={"required_capabilities": ["customer.credit_score"]},
    )

    assert order.validate_recommendation(recommendation) == ()
    assert transport.events == []


def test_side_effect_capability_is_not_executed_by_discovery() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(capability("inventory.reservation.create", "inventory"))
    transport = Transport()
    invoker = CapabilityInvoker(registry, transport)

    discovered = list(registry.find(Intent("inventory.reservation.create")))

    assert discovered[0].metadata["owner"] == "inventory"
    assert transport.events == []
    invoker.invoke(Intent("inventory.reservation.create", {"operation_id": "RES-1"}), entity_id="ORD-1", entity_type="order")
    assert len(transport.events) == 1


def test_sequential_business_dependency_can_stop_before_later_capability() -> None:
    order, transport, _ = build_order()
    discovered = order.discover_requirements(request())
    order.invoker.invoke(
        Intent(discovered["product_validity"].capability_id, request().parameters),
        entity_id="P123",
        entity_type="product",
        context={"correlation_id": "C1", "causation_id": "order-request"},
    )
    publish_fact(transport, "product", False)

    assert order.status.get("C1") is None
    assert len(transport.events) == 2
    assert all(
        event.metadata.get("capability_id") != "pricing.price.retrieve"
        for event in transport.events
        if event.event_type == "capability.invocation"
    )


def test_human_and_agent_paths_share_invocation_boundary() -> None:
    order, agent_transport, _ = build_order()
    order.assess(request(), correlation_id="AGENT", causation_id="order-agent")
    agent_events = list(agent_transport.events)

    human_transport = Transport()
    human_invoker = CapabilityInvoker(order.registry, human_transport)
    human_invoker.invoke(
        Intent("inventory.availability.check", request().parameters),
        entity_id="P123",
        entity_type="product",
        context={"correlation_id": "HUMAN", "causation_id": "human-intent"},
    )

    assert agent_events[1].event_type == human_transport.events[0].event_type
    assert agent_events[1].payload["capability_id"] == human_transport.events[0].payload["capability_id"]
    assert agent_events[1].metadata["causation_id"] != human_transport.events[0].metadata["causation_id"]


def test_order_logic_is_business_specific_not_generic_workflow_runtime() -> None:
    source = inspect.getsource(OrderAutonomousDecision)

    assert "Workflow" not in source
    assert "Planner" not in source
    assert "retry" not in source.lower()
    assert "compensate" not in source.lower()
    assert ".process(" not in source


def test_agent_recommendation_cannot_become_authoritative_action() -> None:
    order, transport, _ = build_order()
    invalid = Recommendation("INVOKE", parameters={"capability": "customer.credit_score"})

    assert order.validate_recommendation(invalid) == ()
    assert transport.events == []
