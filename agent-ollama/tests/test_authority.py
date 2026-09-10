from __future__ import annotations

from agent_core import Action, Agent, Decision, Event, State
from agent_ollama import OllamaReasoner


class InMemoryStateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}
        self.saves = 0

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.saves += 1
        self.states[(state.entity_id, state.entity_type)] = state


class FakeClient:
    def __init__(self, response: str | None = None) -> None:
        self.response = response or '{"recommendations": [{"recommendation_type": "INSPECT", "rationale": "Check it", "parameters": {}, "confidence": 0.8}]}'

    def generate(self, prompt: str) -> str:
        return self.response


class RecommendationPolicy:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.received = []

    def evaluate(self, state: State) -> list[Decision]:
        raise AssertionError("new reasoning must be validated through recommendations")

    def evaluate_recommendations(self, state: State, recommendations) -> list[Decision]:
        self.received.extend(recommendations)
        if not self.allowed:
            return []
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type=recommendations[0].recommendation_type,
                severity="MEDIUM",
                reason=recommendations[0].rationale or "",
            )
        ]


class RecordingExecutor:
    def __init__(self) -> None:
        self.actions = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)


def make_agent(policy, store, executor, client=None):
    return Agent(
        store,
        policy,
        reasoner=OllamaReasoner(client or FakeClient()),
        action_executor=executor,
    )


def make_event() -> Event:
    return Event(
        event_type="measurement.received",
        entity_id="device-1",
        entity_type="device",
        payload={"temperature": 80},
    )


def test_recommendation_requires_policy_approval_before_action() -> None:
    store = InMemoryStateStore()
    policy = RecommendationPolicy(allowed=True)
    executor = RecordingExecutor()

    result = make_agent(policy, store, executor).process(make_event())

    assert len(result.recommendations) == 1
    assert len(policy.received) == 1
    assert len(result.decisions) == 1
    assert len(executor.actions) == 1
    assert store.saves == 1


def test_policy_denial_wins_and_no_action_executes() -> None:
    store = InMemoryStateStore()
    policy = RecommendationPolicy(allowed=False)
    executor = RecordingExecutor()

    result = make_agent(policy, store, executor).process(make_event())

    assert len(result.recommendations) == 1
    assert result.decisions == []
    assert executor.actions == []


def test_malformed_reasoning_output_cannot_create_action() -> None:
    store = InMemoryStateStore()
    policy = RecommendationPolicy(allowed=True)
    executor = RecordingExecutor()

    try:
        make_agent(policy, store, executor, FakeClient("{}")).process(make_event())
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected malformed reasoning output to fail")

    assert policy.received == []
    assert executor.actions == []
