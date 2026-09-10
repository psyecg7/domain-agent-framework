from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import inspect

from agent_core import Action, Decision, Event, Observation, Recommendation, State, decision_to_action


class Transport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Any]] = {}

    def subscribe(self, event_type: str, handler: Any) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers.get(event.event_type, []):
            handler(event)


@dataclass
class InventoryAutonomousDomain:
    """Test-local Inventory behavior; deliberately not a generic agent loop."""

    transport: Transport
    managed_product: str = "P123"
    stock: int = 0
    threshold: int = 10
    state: State = field(init=False)
    observations: dict[str, Observation] = field(default_factory=dict, init=False)
    recommendations: list[Recommendation] = field(default_factory=list, init=False)
    actions: list[Action] = field(default_factory=list, init=False)
    decisions: list[Decision] = field(default_factory=list, init=False)
    operation_status: str = field(default="IDLE", init=False)
    allow_replenishment: bool = field(default=True, init=False)
    replenishment_effects: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.state = State(self.managed_product, "inventory", {"stock": self.stock, "threshold": self.threshold})
        self.transport.subscribe("inventory.level.changed", self.observe)
        self.transport.subscribe("inventory.replenished", self.observe)
        self.transport.subscribe("replenishment.result", self.receive_result)

    def observe(self, event: Event) -> None:
        if event.event_id in self.observations:
            return
        self.observations[event.event_id] = Observation(
            entity_id=event.entity_id,
            entity_type=event.entity_type,
            name="stock",
            value=event.payload["stock"],
            source=event.source,
            metadata={"event_id": event.event_id},
        )
        self.state.apply_observation(self.observations[event.event_id])
        self.stock = event.payload["stock"]

    def reason(self) -> Recommendation | None:
        if self.stock >= self.state.values["threshold"]:
            return None
        recommendation = Recommendation(
            "REPLENISH",
            rationale="Stock is below the Inventory reorder threshold",
            parameters={"product_id": self.managed_product, "quantity": self.state.values["threshold"] - self.stock},
            metadata={"state_version": self.state.version},
        )
        self.recommendations.append(recommendation)
        return recommendation

    def evaluate(self, recommendation: Recommendation | None) -> Decision | None:
        if recommendation is None:
            return None
        if recommendation.metadata.get("state_version") != self.state.version:
            return None
        quantity = recommendation.parameters.get("quantity")
        if not self.allow_replenishment or not isinstance(quantity, int) or quantity <= 0 or quantity > 100:
            return None
        decision = Decision(
            entity_id=self.managed_product,
            entity_type="inventory",
            decision_type="REQUEST_REPLENISHMENT",
            severity="LOW",
            reason=recommendation.rationale or "",
        )
        self.decisions.append(decision)
        return decision

    def execute(self, decision: Decision | None) -> Action | None:
        if decision is None:
            return None
        action = decision_to_action(
            decision,
            decision.decision_type,
            parameters={"quantity": self.state.values["threshold"] - self.stock},
            metadata={"state_version": self.state.version},
        )
        self.actions.append(action)
        self.operation_status = "REQUESTED"
        self.transport.publish(
            Event(
                "replenishment.requested",
                self.managed_product,
                "inventory",
                {"operation_id": action.action_id, "quantity": action.parameters["quantity"]},
                source="inventory",
                metadata={"causation_id": action.action_id},
            )
        )
        return action

    def receive_result(self, event: Event) -> None:
        if self.operation_status != "REQUESTED":
            return
        status = event.payload.get("status")
        if status == "SUCCEEDED":
            self.operation_status = "SUCCEEDED"
        elif status == "FAILED":
            self.operation_status = "FAILED"
        else:
            self.operation_status = "UNKNOWN"


def level_event(stock: int, event_id: str) -> Event:
    return Event(
        "inventory.level.changed",
        "P123",
        "inventory",
        {"stock": stock},
        source="warehouse",
        event_id=event_id,
    )


def test_above_threshold_updates_observation_and_state_without_action() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=12)
    domain.transport.publish(level_event(12, "level-1"))

    assert domain.state.values["stock"] == 12
    assert len(domain.observations) == 1
    assert domain.reason() is None
    assert domain.actions == []


def test_low_stock_produces_recommendation_then_policy_decision_and_action() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=3)
    domain.transport.publish(level_event(3, "level-1"))

    recommendation = domain.reason()
    decision = domain.evaluate(recommendation)
    action = domain.execute(decision)

    assert recommendation is not None
    assert decision is not None
    assert action is not None
    assert len(domain.transport.events) == 2
    assert domain.transport.events[-1].event_type == "replenishment.requested"


def test_policy_rejection_produces_no_action() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=3)
    domain.allow_replenishment = False
    domain.transport.publish(level_event(3, "level-1"))

    recommendation = domain.reason()
    assert recommendation is not None
    assert domain.evaluate(recommendation) is None
    assert domain.execute(None) is None
    assert domain.actions == []


def test_stale_recommendation_is_rejected_against_current_state() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=5)
    domain.transport.publish(level_event(5, "level-1"))
    recommendation = domain.reason()
    domain.transport.publish(level_event(50, "level-2"))

    assert recommendation is not None
    assert domain.evaluate(recommendation) is None
    assert domain.actions == []
    assert domain.state.values["stock"] == 50


def test_duplicate_observation_does_not_create_duplicate_recommendation_or_action() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=3)
    event = level_event(3, "level-1")
    domain.transport.publish(event)
    domain.transport.publish(event)
    recommendation = domain.reason()
    domain.execute(domain.evaluate(recommendation))

    assert len(domain.observations) == 1
    assert domain.state.version == 1
    assert len(domain.actions) == 1


def test_result_event_reenters_same_inventory_cycle() -> None:
    transport = Transport()
    domain = InventoryAutonomousDomain(transport, stock=3)
    transport.publish(level_event(3, "level-1"))
    domain.execute(domain.evaluate(domain.reason()))
    transport.publish(Event("replenishment.result", "P123", "inventory", {"status": "SUCCEEDED"}))
    transport.publish(Event("inventory.replenished", "P123", "inventory", {"stock": 20}))

    assert domain.operation_status == "SUCCEEDED"
    assert domain.state.values["stock"] == 20
    assert domain.reason() is None


def test_external_capability_request_uses_event_boundary() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=3)
    domain.transport.publish(level_event(3, "level-1"))
    action = domain.execute(domain.evaluate(domain.reason()))

    assert action is not None
    assert domain.transport.events[-1].event_type == "replenishment.requested"
    assert domain.transport.events[-1].payload["operation_id"] == action.action_id


def test_failed_action_is_not_successful_decision_execution() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=3)
    domain.transport.publish(level_event(3, "level-1"))
    domain.execute(domain.evaluate(domain.reason()))
    domain.transport.publish(Event("replenishment.result", "P123", "inventory", {"status": "FAILED"}))

    assert domain.decisions[0].decision_type == "REQUEST_REPLENISHMENT"
    assert domain.operation_status == "FAILED"


def test_lost_result_is_unknown_not_failure_or_success() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=3)
    domain.transport.publish(level_event(3, "level-1"))
    domain.execute(domain.evaluate(domain.reason()))

    assert domain.operation_status == "REQUESTED"
    assert domain.operation_status not in {"FAILED", "SUCCEEDED"}
    domain.receive_result(Event("replenishment.result", "P123", "inventory", {"status": "UNKNOWN"}))
    assert domain.operation_status == "UNKNOWN"


def test_external_request_and_autonomous_observation_share_authority_model() -> None:
    domain = InventoryAutonomousDomain(Transport(), stock=20)
    external_request = Event(
        "inventory.availability.requested",
        "P123",
        "inventory",
        {"requested_by": "order"},
        source="order",
    )
    domain.transport.publish(external_request)
    domain.transport.publish(level_event(3, "level-1"))

    assert domain.state.values["stock"] == 3
    assert domain.reason() is not None
    assert "inventory" == domain.state.entity_type


def test_inventory_component_is_not_a_generic_agent_loop() -> None:
    source = inspect.getsource(InventoryAutonomousDomain)

    assert "AgentLoop" not in source
    assert "Planner" not in source
    assert "Workflow" not in source
    assert "Coordinator" not in source
    assert ".process(" not in source
