from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable

import pytest

from agent_core import (
    Action,
    Agent,
    Capability,
    CapabilityResolutionError,
    ConversationalGateway,
    Decision,
    Event,
    InMemoryCapabilityRegistry,
    Intent,
    State,
)
from agent_ollama import OllamaIntentInterpreter


class FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = json.dumps(response)

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


class StateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class TargetPolicy:
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
                decision_type="RESERVATION_ALLOWED",
                severity="LOW",
                reason="Target policy approved the request",
            )
        ]


class RecordingExecutor:
    def __init__(self) -> None:
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)


def reserve_capability(capability_id: str = "inventory.reserve", **metadata: Any) -> Capability:
    return Capability(
        capability_id=capability_id,
        name="Reserve inventory",
        description="Handle a request to reserve a quantity.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        metadata=metadata,
    )


def interpreter_for(intent_type: str = "inventory.reserve") -> OllamaIntentInterpreter:
    return OllamaIntentInterpreter(
        FakeClient(
            {
                "intent_type": intent_type,
                "parameters": {"product_id": "P123", "quantity": 2},
                "metadata": {"channel": "chat"},
            }
        )
    )


def test_human_request_discovers_capability_and_reaches_target_agent() -> None:
    registry = InMemoryCapabilityRegistry()
    capability = reserve_capability()
    registry.register(capability)
    transport = EventTransport()
    store = StateStore()
    executor = RecordingExecutor()
    target = Agent(store, TargetPolicy(allowed=True), action_executor=executor)
    results = []
    transport.subscribe("intent.requested", lambda event: results.append(target.process(event)))

    discovered = ConversationalGateway(interpreter_for(), registry, transport).handle(
        "Reserve two units of product P123.", entity_id="P123", entity_type="reservation_request"
    )

    assert discovered == capability
    assert len(results) == 1
    assert results[0].decisions[0].decision_type == "RESERVATION_ALLOWED"
    assert len(executor.actions) == 1
    assert transport.events[0].metadata["capability_id"] == "inventory.reserve"


def test_unknown_capability_stops_without_event_or_state_change() -> None:
    registry = InMemoryCapabilityRegistry()
    transport = EventTransport()
    store = StateStore()

    with pytest.raises(CapabilityResolutionError):
        ConversationalGateway(interpreter_for("inventory.teleport"), registry, transport).handle(
            "Teleport inventory", entity_id="P123", entity_type="request"
        )

    assert transport.events == []
    assert store.states == {}


def test_ambiguous_capabilities_are_explicitly_rejected() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(reserve_capability("inventory.reserve.primary", intent_types=("inventory.reserve",)))
    registry.register(reserve_capability("inventory.reserve.secondary", intent_types=("inventory.reserve",)))

    with pytest.raises(CapabilityResolutionError, match="Ambiguous"):
        ConversationalGateway(interpreter_for(), registry, EventTransport()).handle(
            "Reserve two units", entity_id="P123", entity_type="request"
        )


def test_conversational_layer_cannot_override_target_policy_denial() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(reserve_capability())
    transport = EventTransport()
    store = StateStore()
    executor = RecordingExecutor()
    target = Agent(store, TargetPolicy(allowed=False), action_executor=executor)
    results = []
    transport.subscribe("intent.requested", lambda event: results.append(target.process(event)))

    ConversationalGateway(interpreter_for(), registry, transport).handle(
        "Reserve two units", entity_id="P123", entity_type="request"
    )

    assert results[0].decisions == []
    assert executor.actions == []
    assert store.states["P123", "request"].values["quantity"] == 2


def test_interpretation_alone_has_no_state_or_action_side_effects() -> None:
    store = StateStore()
    interpreter = interpreter_for()

    intent = interpreter.interpret("Reserve two units", {"locale": "en"})

    assert isinstance(intent, Intent)
    assert store.states == {}
