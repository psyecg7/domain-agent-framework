from agent_app._local import local_agent
from agent_core import Decision, Event


def test_private_local_composition_keeps_policy_before_action_execution() -> None:
    executed = []

    def decide(state):
        if state.values.get("temperature", 0) > 40:
            return [Decision(state.entity_id, state.entity_type, "INVESTIGATE", "MEDIUM", "threshold exceeded")]
        return []

    agent = local_agent(decide, on_action=executed.append)

    result = agent.process(Event("measurement.received", "sensor-1", "sensor", {"temperature": 42}, source="telemetry"))

    assert [decision.decision_type for decision in result.decisions] == ["INVESTIGATE"]
    assert [action.action_type for action in result.actions] == ["INVESTIGATE"]
    assert executed == result.actions


def test_private_local_composition_does_not_execute_without_a_decision() -> None:
    executed = []
    agent = local_agent(lambda state: [], on_action=executed.append)

    result = agent.process(Event("measurement.received", "sensor-1", "sensor", {"temperature": 20}, source="telemetry"))

    assert result.actions == []
    assert executed == []
