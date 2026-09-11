import pytest

import agent_app
import agent_core
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


def test_application_composition_is_not_exported_by_core() -> None:
    application_only = (
        "CapabilityInvoker",
        "ConversationalGateway",
        "DeterministicResponseInterpreter",
        "ResponseInterpreter",
    )

    for name in application_only:
        assert hasattr(agent_app, name)
        assert not hasattr(agent_core, name)


def test_agent_app_emits_payload_free_lifecycle_events_and_health_counters() -> None:
    observed = []
    app = AgentApp(observers=[observed.append])

    @app.policy("measurement.received")
    def investigate(state):
        return app.decide("INVESTIGATE")

    @app.action("INVESTIGATE")
    def notify(action):
        return None

    app.process(event(42))

    assert [item.phase for item in observed] == ["started", "succeeded"]
    assert observed[1].decision_count == 1
    assert observed[1].action_count == 1
    assert not hasattr(observed[1], "payload")
    assert app.health().processed_events == 1
    assert app.health().succeeded_events == 1
    assert app.health().status == "ok"


def test_observer_failures_do_not_change_application_processing() -> None:
    def broken_observer(_):
        raise RuntimeError("telemetry unavailable")

    app = AgentApp(observers=[broken_observer])

    @app.policy("measurement.received")
    def no_action(state):
        return None

    assert app.process(event(20)).actions == []
    assert app.health().succeeded_events == 1


def test_agent_app_records_failed_processing_without_suppressing_error() -> None:
    app = AgentApp()

    with pytest.raises(AppConfigurationError):
        app.process(event(20))

    assert app.health().processed_events == 1
    assert app.health().failed_events == 1
    assert app.health().status == "degraded"
