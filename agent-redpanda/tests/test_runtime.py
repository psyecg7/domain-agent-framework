from __future__ import annotations

from agent_core import Action, Agent, Decision, Event, State, decision_to_action
from agent_redpanda.mapper import RedpandaEventMapper
from agent_redpanda.producer import RedpandaProducer
from agent_redpanda.runtime import RedpandaAgentRuntime


class InMemoryStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class InventoryPolicyEngine:
    def evaluate(self, state: State) -> list[Decision]:
        stock = state.values.get("available_stock")
        if isinstance(stock, (int, float)) and stock < 5:
            return [
                Decision(
                    entity_id=state.entity_id,
                    entity_type=state.entity_type,
                    decision_type="INVESTIGATE",
                    severity="MEDIUM",
                    reason="Inventory below reorder threshold",
                    confidence=None,
                )
            ]
        return []


class FakeProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict]] = []

    def publish(self, *, topic: str, key: str | None, value: dict) -> None:
        self.messages.append((topic, key or "", value))


class NoopReasoner:
    def reason(self, state: State, decisions: list[Decision]) -> list[Decision]:
        return decisions


def test_redpanda_runtime_processes_event_and_emits_messages() -> None:
    store = InMemoryStateStore()
    producer = FakeProducer()
    agent = Agent(
        store,
        InventoryPolicyEngine(),
        reasoner=NoopReasoner(),
        action_executor=None,
        action_factory=lambda decision: decision_to_action(
            decision,
            "CREATE_REPLENISHMENT",
            parameters={"decision_id": decision.decision_id, "reason": decision.reason},
        ),
    )
    runtime = RedpandaAgentRuntime(
        agent,
        mapper=RedpandaEventMapper(),
        producer=producer,
        decision_topic="agent.decisions",
        action_topic="agent.actions",
    )

    record = {
        "event_id": "evt-001",
        "event_type": "inventory.stock_changed",
        "entity_id": "sku-9",
        "entity_type": "inventory_item",
        "source": "inventory_system",
        "occurred_at": "2024-01-01T00:00:00+00:00",
        "metadata": {"correlation_id": "flow-1"},
        "available_stock": 3,
    }

    result = runtime.process_record(record)

    assert result.state.values["available_stock"] == 3
    assert result.decisions[0].decision_type == "INVESTIGATE"
    assert result.actions[0].action_type == "CREATE_REPLENISHMENT"
    assert producer.messages[0][0] == "agent.decisions"
    assert producer.messages[1][0] == "agent.actions"
    assert producer.messages[0][2]["metadata"]["correlation_id"] == "flow-1"
    assert producer.messages[1][2]["metadata"]["correlation_id"] == "flow-1"
    assert producer.messages[1][2]["metadata"]["causation_id"] == result.event.event_id


def test_redpanda_runtime_supports_other_event_types_without_domain_specific_logic() -> None:
    store = InMemoryStateStore()
    producer = FakeProducer()

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

    agent = Agent(
        store,
        RiskPolicyEngine(),
        reasoner=NoopReasoner(),
        action_executor=None,
        action_factory=lambda decision: decision_to_action(
            decision,
            "HOLD_ORDER",
            parameters={"decision_id": decision.decision_id, "risk": "high"},
        ),
    )

    runtime = RedpandaAgentRuntime(
        agent,
        mapper=RedpandaEventMapper(),
        producer=producer,
        decision_topic="agent.decisions",
        action_topic="agent.actions",
    )

    result = runtime.process_record({
        "event_id": "evt-002",
        "event_type": "order.created",
        "entity_id": "order-77",
        "entity_type": "order",
        "source": "order_service",
        "occurred_at": "2024-01-01T00:00:00+00:00",
        "risk_score": 0.92,
    })

    assert result.decisions[0].decision_type == "HIGH_RISK_ORDER"
    assert result.actions[0].action_type == "HOLD_ORDER"
    assert producer.messages[0][0] == "agent.decisions"
    assert producer.messages[1][0] == "agent.actions"
