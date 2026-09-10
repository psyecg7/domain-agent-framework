import pytest

from agent_core import Action, ActionConstructionError, Agent, Decision, Event, State, decision_to_action


def decision() -> Decision:
    return Decision(
        entity_id="ORD-1",
        entity_type="order",
        decision_type="APPROVE",
        severity="LOW",
        reason="Policy approved",
    )


def test_action_cannot_be_constructed_directly() -> None:
    with pytest.raises(ActionConstructionError):
        Action("APPROVE", "ORD-1", "order")


@pytest.mark.parametrize("value", [{}, object(), None])
def test_decision_to_action_rejects_non_decision_values(value) -> None:
    with pytest.raises(ActionConstructionError):
        decision_to_action(value, "APPROVE")


def test_decision_to_action_inherits_the_decision_target() -> None:
    source = decision()

    action = decision_to_action(
        source,
        "CREATE_ORDER",
        parameters={"requested_entity_id": "OTHER-ORDER", "requested_entity_type": "inventory"},
    )

    assert action.entity_id == source.entity_id
    assert action.entity_type == source.entity_type
    assert action.parameters["requested_entity_id"] == "OTHER-ORDER"


class Store:
    def __init__(self) -> None:
        self.states = {}

    def get(self, entity_id: str, entity_type: str):
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[state.entity_id, state.entity_type] = state


class Policy:
    def evaluate(self, state: State) -> list[Decision]:
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type="APPROVE",
                severity="LOW",
                reason="Policy approved",
            )
        ]


def test_agent_process_factory_cannot_spoof_the_decision_target() -> None:
    def action_factory(authorizing_decision: Decision) -> Action:
        return decision_to_action(
            authorizing_decision,
            "CREATE_ORDER",
            parameters={"requested_entity_id": "OTHER-ORDER", "requested_entity_type": "inventory"},
        )

    result = Agent(Store(), Policy(), action_factory=action_factory).process(Event(
        "order.requested", "ORD-1", "order", payload={}
    ))

    assert result.actions[0].entity_id == result.decisions[0].entity_id == "ORD-1"
    assert result.actions[0].entity_type == result.decisions[0].entity_type == "order"
    assert result.actions[0].parameters["requested_entity_id"] == "OTHER-ORDER"
