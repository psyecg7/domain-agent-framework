from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
import inspect

from agent_app import CapabilityInvoker
from agent_core import Action, Agent, Capability, Decision, Event, InMemoryCapabilityRegistry, Intent, State


class DomainStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class DeferredEventTransport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Callable[[Event], Any]]] = defaultdict(list)
        self.pending: list[Event] = []

    def subscribe(self, event_type: str, handler: Callable[[Event], Any]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        self.pending.append(event)

    def deliver(self, event: Event) -> None:
        for handler in self.handlers[event.event_type]:
            handler(event)

    def deliver_next(self, event_type: str) -> Event:
        event = next(event for event in self.pending if event.event_type == event_type)
        self.pending.remove(event)
        self.deliver(event)
        return event

    def deliver_all(self, event_type: str) -> None:
        while any(event.event_type == event_type for event in self.pending):
            self.deliver_next(event_type)


class DomainPolicy:
    def __init__(self, fact_name: str, allowed: Callable[[Any], bool], decision_type: str) -> None:
        self.fact_name = fact_name
        self.allowed = allowed
        self.decision_type = decision_type
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        if not self.allowed(state.values.get(self.fact_name)):
            return []
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type=self.decision_type,
                severity="LOW",
                reason=f"{self.fact_name} passed local domain policy",
            )
        ]


class RecordingExecutor:
    def __init__(self) -> None:
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)


class OrderPurchaseAssessment:
    """Explicit Order-owned business aggregation for this experiment."""

    required_domains = ("pim", "inventory", "pricing")

    def __init__(self, invoker: CapabilityInvoker, transport: DeferredEventTransport) -> None:
        self.invoker = invoker
        self.transport = transport
        self.facts: dict[str, dict[str, dict[str, Any]]] = {}
        self.final_events: list[Event] = []
        transport.subscribe("pim.product.result", self._accept_result)
        transport.subscribe("inventory.availability.result", self._accept_result)
        transport.subscribe("pricing.price.result", self._accept_result)

    def request_can_buy(self, order_id: str, product_id: str, correlation_id: str) -> None:
        requests = (
            ("product.information.retrieve", "pim.request"),
            ("inventory.availability.check", "inventory.request"),
            ("pricing.price.retrieve", "pricing.request"),
        )
        for intent_type, causation_id in requests:
            self.invoker.invoke(
                Intent(intent_type, {"order_id": order_id, "product_id": product_id}),
                entity_id=product_id,
                entity_type="product_request",
                context={"correlation_id": correlation_id, "causation_id": causation_id},
            )

    def _accept_result(self, event: Event) -> None:
        correlation_id = event.metadata.get("correlation_id")
        domain = event.payload.get("domain")
        if not isinstance(correlation_id, str) or not isinstance(domain, str):
            return
        facts = self.facts.setdefault(correlation_id, {})
        if domain in facts:
            return
        facts[domain] = dict(event.payload)
        if any(required not in facts for required in self.required_domains):
            return

        can_buy = all(facts[required].get("valid") is True for required in self.required_domains)
        final_event = Event(
                event_type="order.can_buy.result",
                entity_id=facts["pim"]["product_id"],
                entity_type="order",
                payload={
                    "product_id": facts["pim"]["product_id"],
                    "can_buy": can_buy,
                    "facts": facts,
                },
                source="order",
                metadata={
                    "correlation_id": correlation_id,
                    "causation_id": event.event_id,
                },
            )
        self.final_events.append(final_event)
        self.transport.publish(final_event)


def make_capability(capability_id: str, owner: str) -> Capability:
    return Capability(
        capability_id=capability_id,
        name=f"{owner} capability",
        description=f"A capability owned by {owner}.",
        metadata={"owner": owner, "intent_types": (capability_id,)},
    )


def build_composition(
    *,
    product_status: str = "ACTIVE",
    available_stock: int = 17,
    price: float = 12.50,
) -> tuple[
    DeferredEventTransport,
    OrderPurchaseAssessment,
    dict[str, DomainStateStore],
    dict[str, DomainPolicy],
    dict[str, RecordingExecutor],
]:
    registry = InMemoryCapabilityRegistry()
    for capability_id, owner in (
        ("product.information.retrieve", "pim"),
        ("inventory.availability.check", "inventory"),
        ("pricing.price.retrieve", "pricing"),
    ):
        registry.register(make_capability(capability_id, owner))
    registry.register(make_capability("supplier.stock.replenish", "supplier"))

    transport = DeferredEventTransport()
    invoker = CapabilityInvoker(registry, transport)
    stores = {
        "pim": DomainStateStore(),
        "inventory": DomainStateStore(),
        "pricing": DomainStateStore(),
        "order": DomainStateStore(),
    }
    stores["pim"].save(State("P123", "product", {"status": product_status}))
    stores["inventory"].save(State("P123", "inventory", {"available_stock": available_stock}))
    stores["pricing"].save(State("P123", "price", {"price": price}))
    policies = {
        "pim": DomainPolicy("status", lambda value: value == "ACTIVE", "PRODUCT_VALID"),
        "inventory": DomainPolicy("available_stock", lambda value: isinstance(value, int) and value > 0, "IN_STOCK"),
        "pricing": DomainPolicy("price", lambda value: isinstance(value, (int, float)) and value > 0, "PRICE_VALID"),
    }
    executors = {domain: RecordingExecutor() for domain in policies}
    agents = {
        domain: Agent(stores[domain], policies[domain], action_executor=executors[domain])
        for domain in policies
    }

    result_types = {
        "pim": "pim.product.result",
        "inventory": "inventory.availability.result",
        "pricing": "pricing.price.result",
    }

    def route_to_domain(event: Event) -> None:
        intent_type = event.payload["intent"]["intent_type"]
        domain = {"product.information.retrieve": "pim", "inventory.availability.check": "inventory", "pricing.price.retrieve": "pricing"}[intent_type]
        domain_event = Event(
            event_type=event.event_type,
            entity_id=event.entity_id,
            entity_type={"pim": "product", "inventory": "inventory", "pricing": "price"}[domain],
            payload={"capability_id": event.payload["capability_id"], "request": event.payload["intent"]},
            source=event.source,
            metadata=dict(event.metadata),
        )
        result = agents[domain].process(domain_event)
        state = stores[domain].get("P123", {"pim": "product", "inventory": "inventory", "pricing": "price"}[domain])
        assert state is not None
        valid = bool(result.decisions)
        payload = {
            "domain": domain,
            "product_id": "P123",
            "valid": valid,
            "value": state.values.get({"pim": "status", "inventory": "available_stock", "pricing": "price"}[domain]),
        }
        transport.publish(
            Event(
                event_type=result_types[domain],
                entity_id="P123",
                entity_type=domain,
                payload=payload,
                source=domain,
                metadata={
                    "correlation_id": event.metadata["correlation_id"],
                    "causation_id": result.actions[0].action_id if result.actions else event.event_id,
                },
            )
        )

    transport.subscribe("capability.invocation", route_to_domain)
    assessment = OrderPurchaseAssessment(invoker, transport)
    return transport, assessment, stores, policies, executors


def test_three_authoritative_domains_compose_out_of_order() -> None:
    transport, assessment, stores, policies, executors = build_composition()
    assessment.request_can_buy("ORD-1", "P123", "buy-1")

    assert [event.metadata["capability_id"] for event in transport.events[:3]] == [
        "product.information.retrieve",
        "inventory.availability.check",
        "pricing.price.retrieve",
    ]
    assert all(event.metadata["correlation_id"] == "buy-1" for event in transport.events[:3])
    assert len(transport.pending) == 3
    transport.deliver_all("capability.invocation")

    transport.deliver_next("pricing.price.result")
    transport.deliver_next("pim.product.result")
    transport.deliver_next("inventory.availability.result")

    assert len(assessment.final_events) == 1
    assert assessment.final_events[0].payload["can_buy"] is True
    assert assessment.final_events[0].metadata["correlation_id"] == "buy-1"
    assert assessment.final_events[0].metadata["causation_id"] in {
        event.event_id for event in transport.events if event.event_type.endswith(".result")
    }
    assert all(policy.calls == 1 for policy in policies.values())
    assert all(len(executor.actions) == 1 for executor in executors.values())
    assert stores["order"].states == {}


def test_each_domain_can_reject_and_order_result_is_negative() -> None:
    cases: tuple[tuple[str, int, float], ...] = (
        ("DISCONTINUED", 17, 12.50),
        ("ACTIVE", 0, 12.50),
        ("ACTIVE", 17, 0),
    )
    for product_status, available_stock, price in cases:
        transport, assessment, _, _, executors = build_composition(
            product_status=product_status,
            available_stock=available_stock,
            price=price,
        )
        assessment.request_can_buy("ORD-1", "P123", "buy-failure")
        transport.deliver_all("capability.invocation")
        for result_type in (
            "pricing.price.result",
            "inventory.availability.result",
            "pim.product.result",
        ):
            transport.deliver_next(result_type)
        assert assessment.final_events[-1].payload["can_buy"] is False
        assert sum(len(executor.actions) for executor in executors.values()) == 2


def test_missing_result_does_not_fabricate_final_business_answer() -> None:
    transport, assessment, _, _, _ = build_composition()
    assessment.request_can_buy("ORD-1", "P123", "buy-late")
    transport.deliver_all("capability.invocation")

    transport.deliver_next("pim.product.result")
    transport.deliver_next("inventory.availability.result")

    assert assessment.final_events == []


def test_result_arrival_does_not_trigger_capability_chaining() -> None:
    transport, assessment, _, _, _ = build_composition()
    assessment.request_can_buy("ORD-1", "P123", "buy-one-hop")
    invocation_count = len([event for event in transport.events if event.event_type == "capability.invocation"])
    transport.deliver_all("capability.invocation")

    transport.deliver_next("pricing.price.result")
    transport.deliver_next("pim.product.result")
    transport.deliver_next("inventory.availability.result")

    assert len([event for event in transport.events if event.event_type == "capability.invocation"]) == invocation_count == 3
    assert all(event.metadata.get("capability_id") != "supplier.stock.replenish" for event in transport.events)
    assert len(assessment.final_events) == 1


def test_ownership_and_no_direct_agent_dependency_are_explicit() -> None:
    _, _, _, _, _ = build_composition()
    registry = InMemoryCapabilityRegistry()
    registry.register(make_capability("product.information.retrieve", "pim"))
    registry.register(make_capability("inventory.availability.check", "inventory"))
    registry.register(make_capability("pricing.price.retrieve", "pricing"))

    assert registry.get("product.information.retrieve").metadata["owner"] == "pim"
    assert registry.get("inventory.availability.check").metadata["owner"] == "inventory"
    assert registry.get("pricing.price.retrieve").metadata["owner"] == "pricing"
    source = inspect.getsource(OrderPurchaseAssessment)
    assert "Agent" not in source
    assert "StateStore" not in source
    assert ".process(" not in source
