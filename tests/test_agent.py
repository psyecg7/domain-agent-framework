from __future__ import annotations

from agent_core import Action, Agent, Decision, Event, Policy, State, decision_to_action


class InMemoryStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class ThresholdPolicyEngine:
    def evaluate(self, state: State) -> list[Decision]:
        value = state.values.get("value")
        if isinstance(value, (int, float)) and value > 40:
            return [
                Decision(
                    entity_id=state.entity_id,
                    entity_type=state.entity_type,
                    decision_type="INVESTIGATE",
                    severity="MEDIUM",
                    reason="Value exceeds threshold",
                    confidence=None,
                )
            ]
        return []


class NoopReasoner:
    def reason(self, state: State, decisions: list[Decision]) -> list[Decision]:
        return decisions


class RecordingActionExecutor:
    def __init__(self) -> None:
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)


def test_agent_process_creates_state_and_action() -> None:
    store = InMemoryStateStore()
    policy_engine = ThresholdPolicyEngine()
    executor = RecordingActionExecutor()
    agent = Agent(store, policy_engine, reasoner=NoopReasoner(), action_executor=executor)

    event = Event(
        event_type="measurement_received",
        entity_id="sensor-9",
        entity_type="sensor",
        payload={"value": 42},
        source="telemetry",
    )

    result = agent.process(event)

    assert result.event.event_id == event.event_id
    assert result.observation.entity_id == event.entity_id
    assert result.observation.entity_type == event.entity_type
    assert result.observation.name == "value"
    assert result.observation.value == 42

    assert result.state.entity_id == "sensor-9"
    assert result.state.entity_type == "sensor"
    assert result.state.values["value"] == 42
    assert result.state.version == 1

    assert len(result.decisions) == 1
    assert result.decisions[0].decision_type == "INVESTIGATE"
    assert len(result.actions) == 1
    assert result.actions[0].action_type == "INVESTIGATE"

    stored = store.get("sensor-9", "sensor")
    assert stored is not None
    assert stored.values["value"] == 42
    assert stored.version == 1

    assert len(executor.actions) == 1
    assert executor.actions[0].action_type == "INVESTIGATE"


def test_agent_propagates_event_idempotency_key_to_action() -> None:
    agent = Agent(InMemoryStateStore(), ThresholdPolicyEngine())

    result = agent.process(Event(
        event_type="measurement_received",
        entity_id="sensor-9",
        entity_type="sensor",
        payload={"value": 42},
        idempotency_key="measurement-42",
    ))

    assert result.actions[0].idempotency_key == "measurement-42"


def test_agent_supports_multiple_domains_with_same_runtime() -> None:
    class InventoryPolicyEngine:
        def evaluate(self, state: State) -> list[Decision]:
            available_stock = state.values.get("available_stock")
            if isinstance(available_stock, (int, float)) and available_stock < 5:
                return [
                    Decision(
                        entity_id=state.entity_id,
                        entity_type=state.entity_type,
                        decision_type="INVESTIGATE",
                        severity="MEDIUM",
                        reason="Inventory is below reorder threshold",
                        confidence=None,
                    )
                ]
            return []

    inventory_store = InMemoryStateStore()
    inventory_policy = InventoryPolicyEngine()
    inventory_executor = RecordingActionExecutor()

    inventory_agent = Agent(
        inventory_store,
        inventory_policy,
        reasoner=NoopReasoner(),
        action_executor=inventory_executor,
        action_factory=lambda decision: decision_to_action(
            decision,
            "CREATE_REPLENISHMENT",
            parameters={
                "decision_id": decision.decision_id,
                "reason": decision.reason,
                "severity": decision.severity,
            },
        ),
    )

    inventory_event = Event(
        event_type="inventory.stock_changed",
        entity_id="sku-42",
        entity_type="inventory_item",
        payload={"available_stock": 3},
        source="inventory_system",
    )

    inventory_result = inventory_agent.process(inventory_event)

    assert inventory_result.state.values["available_stock"] == 3
    assert inventory_result.decisions[0].decision_type == "INVESTIGATE"
    assert inventory_result.actions[0].action_type == "CREATE_REPLENISHMENT"

    class RiskPolicyEngine:
        def evaluate(self, state: State) -> list[Decision]:
            risk_score = state.values.get("risk_score")
            if isinstance(risk_score, (int, float)) and risk_score > 0.8:
                return [
                    Decision(
                        entity_id=state.entity_id,
                        entity_type=state.entity_type,
                        decision_type="HIGH_RISK_ORDER",
                        severity="HIGH",
                        reason="Risk threshold exceeded",
                        confidence=None,
                    )
                ]
            return []

    order_store = InMemoryStateStore()
    order_policy = RiskPolicyEngine()
    order_executor = RecordingActionExecutor()

    def risk_action_factory(decision: Decision) -> Action:
        return decision_to_action(
            decision,
            "HOLD_ORDER",
            parameters={
                "decision_id": decision.decision_id,
                "risk": "high",
            },
        )

    order_agent = Agent(
        order_store,
        order_policy,
        reasoner=NoopReasoner(),
        action_executor=order_executor,
        action_factory=risk_action_factory,
    )

    order_event = Event(
        event_type="order.created",
        entity_id="order-77",
        entity_type="order",
        payload={"risk_score": 0.92},
        source="order_service",
    )

    order_result = order_agent.process(order_event)

    assert order_result.decisions[0].decision_type == "HIGH_RISK_ORDER"
    assert order_result.actions[0].action_type == "HOLD_ORDER"
    assert order_result.actions[0].parameters["risk"] == "high"


def test_agent_allows_application_level_action_mapping() -> None:
    store = InMemoryStateStore()
    policy_engine = ThresholdPolicyEngine()
    executor = RecordingActionExecutor()

    def map_decision_to_action(decision: Decision) -> Action:
        if decision.decision_type == "INVESTIGATE":
            return decision_to_action(
                decision,
                "REQUEST_INVESTIGATION",
                parameters={"decision_id": decision.decision_id, "reason": decision.reason},
            )
        return decision_to_action(
            decision,
            decision.decision_type,
            parameters={"decision_id": decision.decision_id},
        )

    agent = Agent(
        store,
        policy_engine,
        reasoner=NoopReasoner(),
        action_executor=executor,
        action_factory=map_decision_to_action,
    )

    result = agent.process(
        Event(
            event_type="measurement_received",
            entity_id="sensor-5",
            entity_type="sensor",
            payload={"value": 42},
            source="telemetry",
        )
    )

    assert len(result.actions) == 1
    assert result.actions[0].action_type == "REQUEST_INVESTIGATION"
    assert len(executor.actions) == 1
    assert executor.actions[0].action_type == "REQUEST_INVESTIGATION"
