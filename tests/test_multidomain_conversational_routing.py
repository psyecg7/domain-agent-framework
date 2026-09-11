from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable

from agent_app import ConversationalGateway
from agent_core import (
    Action,
    Agent,
    Capability,
    Decision,
    Event,
    InMemoryCapabilityRegistry,
    State,
)
from agent_ollama import OllamaIntentInterpreter


class FakeClient:
    def __init__(self, intent_type: str) -> None:
        self.response = json.dumps(
            {
                "intent_type": intent_type,
                "parameters": {"product_id": "SKU-123", "order_id": "ORD-123"},
                "metadata": {"channel": "chat"},
            }
        )

    def generate(self, prompt: str) -> str:
        return self.response


class EventTransport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers[event.event_type]:
            handler(event)


def interpreter_for(intent_type: str) -> OllamaIntentInterpreter:
    return OllamaIntentInterpreter(FakeClient(intent_type))


class StateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class DomainPolicy:
    def __init__(self, decision_type: str) -> None:
        self.decision_type = decision_type
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type=self.decision_type,
                severity="LOW",
                reason="Local domain policy accepted the request",
            )
        ]


class ResultEventExecutor:
    def __init__(self, transport: EventTransport, result_type: str, source: str) -> None:
        self.transport = transport
        self.result_type = result_type
        self.source = source
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)
        self.transport.publish(
            Event(
                event_type=self.result_type,
                entity_id=action.entity_id,
                entity_type="result",
                payload={"action_type": action.action_type},
                source=self.source,
                metadata={
                    "correlation_id": "multi-domain-flow",
                    "causation_id": action.action_id,
                },
            )
        )


def test_one_gateway_routes_three_registered_domains_without_domain_logic() -> None:
    registry = InMemoryCapabilityRegistry()
    transport = EventTransport()
    routed_results: dict[str, list] = {"inventory": [], "pim": [], "order": []}
    result_events: list[Event] = []

    domain_specs = (
        ("inventory", "inventory.check_availability", "INVENTORY_CHECKED", "inventory.result"),
        ("pim", "pim.get_product", "PRODUCT_RETURNED", "pim.result"),
        ("order", "order.get_status", "ORDER_STATUS_RETURNED", "order.result"),
    )

    for owner, capability_id, decision_type, result_type in domain_specs:
        capability = Capability(
            capability_id=capability_id,
            name=f"{owner} operation",
            description="A domain-owned operation exposed as an intent capability.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            metadata={"owner": owner},
        )
        registry.register(capability)
        policy = DomainPolicy(decision_type)
        executor = ResultEventExecutor(transport, result_type, owner)
        agent = Agent(StateStore(), policy, action_executor=executor, capabilities=(capability,))

        def route(event: Event, *, expected=capability_id, key=owner, target=agent) -> None:
            if event.metadata.get("capability_id") == expected:
                routed_results[key].append(target.process(event))

        transport.subscribe("intent.requested", route)
        transport.subscribe(result_type, result_events.append)

    requests = (
        ("inventory.check_availability", "I want to know whether SKU-123 is available."),
        ("pim.get_product", "Give me the information for SKU-123."),
        ("order.get_status", "Where is order ORD-123?"),
    )
    for intent_type, request in requests:
        gateway = ConversationalGateway(interpreter_for(intent_type), registry, transport)
        gateway.handle(
            request,
            entity_id="SKU-123" if intent_type != "order.get_status" else "ORD-123",
            entity_type="request",
            context={"correlation_id": "multi-domain-flow", "request_id": f"request-{intent_type}"},
        )

    assert [len(routed_results[owner]) for owner, *_ in domain_specs] == [1, 1, 1]
    assert [result.decisions[0].decision_type for result in routed_results["inventory"]] == ["INVENTORY_CHECKED"]
    assert [result.decisions[0].decision_type for result in routed_results["pim"]] == ["PRODUCT_RETURNED"]
    assert [result.decisions[0].decision_type for result in routed_results["order"]] == ["ORDER_STATUS_RETURNED"]
    assert [event.event_type for event in result_events] == ["inventory.result", "pim.result", "order.result"]
    assert all(event.metadata["correlation_id"] == "multi-domain-flow" for event in result_events)
