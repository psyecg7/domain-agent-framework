import pytest

from agent_app import AgentApp, AppConfigurationError
from agent_core import Event


def event(temperature: int) -> Event:
    return Event("measurement.received", "sensor-1", "sensor", {"temperature": temperature}, source="telemetry")


def test_agent_app_has_one_local_policy_to_action_path() -> None:
    app = AgentApp()
    executed = []

    @app.policy("measurement.received")
    def investigate(state):
        if state.values.get("temperature", 0) > 40:
            return app.decide("INVESTIGATE", severity="MEDIUM", reason="threshold exceeded")
        return None

    @app.action("INVESTIGATE")
    def notify(action):
        executed.append(action)

    result = app.process(event(42))

    assert [decision.decision_type for decision in result.decisions] == ["INVESTIGATE"]
    assert [action.action_type for action in result.actions] == ["INVESTIGATE"]
    assert executed == result.actions


def test_agent_app_does_not_execute_when_policy_makes_no_decision() -> None:
    app = AgentApp()
    executed = []

    @app.policy("measurement.received")
    def deny_by_omission(state):
        return None

    @app.action("INVESTIGATE")
    def notify(action):
        executed.append(action)

    assert app.process(event(20)).actions == []
    assert executed == []


def test_agent_app_decision_spec_binds_to_explicit_policy_state_not_ambient_context() -> None:
    app = AgentApp()
    declaration = app.decide("INVESTIGATE", severity="MEDIUM", reason="threshold exceeded")
    executed = []

    @app.policy("measurement.received")
    def investigate(state):
        return declaration

    @app.action("INVESTIGATE")
    def notify(action):
        executed.append(action)

    first = app.process(Event("measurement.received", "sensor-a", "sensor", {}, source="telemetry"))
    second = app.process(Event("measurement.received", "sensor-b", "sensor", {}, source="telemetry"))

    assert not hasattr(declaration, "entity_id")
    assert [action.entity_id for action in first.actions + second.actions] == ["sensor-a", "sensor-b"]
    assert [action.entity_id for action in executed] == ["sensor-a", "sensor-b"]


def test_agent_app_requires_explicit_policy_and_action_registration() -> None:
    app = AgentApp()
    with pytest.raises(AppConfigurationError, match="No policy"):
        app.process(event(42))

    @app.policy("measurement.received")
    def investigate(state):
        return app.decide("INVESTIGATE")

    with pytest.raises(AppConfigurationError, match="No action handler"):
        app.process(event(42))
