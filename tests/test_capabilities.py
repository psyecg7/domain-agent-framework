from __future__ import annotations

from collections import defaultdict
from typing import Callable

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


class StateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}
        self.saves = 0

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.saves += 1
        self.states[(state.entity_id, state.entity_type)] = state


class EventTransport:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in self.handlers[event.event_type]:
            handler(event)


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
                decision_type="RESERVATION_ACCEPTED",
                severity="LOW",
                reason="Target policy accepted the request",
            )
        ]


class RecordingExecutor:
    def __init__(self) -> None:
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)


def reservation_capability() -> Capability:
    return Capability(
        capability_id="inventory.reserve",
        name="Reserve inventory",
        description="Accept a request to reserve an amount of a resource.",
        input_schema={"type": "object", "required": ["product_id", "quantity"]},
        output_schema={"type": "object", "required": ["reservation_id"]},
        metadata={"owner": "inventory", "version": "1"},
    )


def test_agent_exposes_only_declarative_capabilities() -> None:
    agent = Agent(StateStore(), TargetPolicy(allowed=True), capabilities=(reservation_capability(),))

    exposed = agent.capabilities()

    assert exposed == (reservation_capability(),)
    assert exposed[0].metadata["owner"] == "inventory"
    assert not hasattr(exposed[0], "execute")


def test_registry_finds_matching_capability() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())

    matches = registry.find(Intent("inventory.reserve", {"product_id": "P123", "quantity": 2}))

    assert [capability.capability_id for capability in matches] == ["inventory.reserve"]


def test_unsupported_intent_has_no_match() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())

    assert registry.find(Intent("inventory.release")) == ()


def test_discovery_has_no_execution_or_state_side_effect() -> None:
    store = StateStore()
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())

    assert registry.find(Intent("inventory.reserve"))
    assert store.states == {}
    assert store.saves == 0


def test_discovery_does_not_imply_authorization() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())
    authorized = False

    discovered = registry.find(Intent("inventory.reserve"))

    assert discovered
    assert authorized is False


def test_capability_invocation_remains_event_driven_and_target_policy_is_authoritative() -> None:
    registry = InMemoryCapabilityRegistry()
    capability = reservation_capability()
    registry.register(capability)
    transport = EventTransport()
    target_store = StateStore()
    target_policy = TargetPolicy(allowed=False)
    target_executor = RecordingExecutor()
    target_agent = Agent(target_store, target_policy, action_executor=target_executor)
    results = []
    transport.subscribe("intent.requested", lambda event: results.append(target_agent.process(event)))

    intent = Intent("inventory.reserve", {"product_id": "P123", "quantity": 2})
    assert registry.find(intent) == (capability,)
    transport.publish(
        Event(
            event_type="intent.requested",
            entity_id="P123",
            entity_type="reservation_request",
            payload={"intent_type": intent.intent_type, **intent.parameters},
            metadata={"capability_id": capability.capability_id},
        )
    )

    assert len(results) == 1
    assert target_policy.calls == 1
    assert results[0].decisions == []
    assert target_executor.actions == []
    assert target_store.get("P123", "reservation_request") is not None
