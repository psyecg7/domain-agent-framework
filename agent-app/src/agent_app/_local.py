"""Private local composition helpers for :mod:`agent_app`.

These helpers support ``AgentApp``'s in-memory, local-first composition. They
are implementation details of this package, not part of either the public
``agent-app`` or ``agent-core`` API.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from agent_core import Action, Agent, Decision, State


class InMemoryStateStore:
    """Process-local StateStore for AgentApp's local execution mode."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self._states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self._states[(state.entity_id, state.entity_type)] = state


class FunctionPolicyEngine:
    """Adapt AgentApp's deterministic policy function to PolicyEngine."""

    def __init__(self, decide: Callable[[State], Iterable[Decision]]) -> None:
        self._decide = decide

    def evaluate(self, state: State) -> list[Decision]:
        decisions = list(self._decide(state))
        if not all(isinstance(decision, Decision) for decision in decisions):
            raise TypeError("An AgentApp policy must return Decision instances")
        return decisions


class FunctionActionExecutor:
    """Adapt AgentApp's registered local handler to ActionExecutor."""

    def __init__(self, on_action: Callable[[Action], None]) -> None:
        self._on_action = on_action

    def execute(self, action: Action) -> None:
        self._on_action(action)


def local_agent(
    decide: Callable[[State], Iterable[Decision]],
    *,
    on_action: Callable[[Action], None] | None = None,
) -> Agent:
    """Create AgentApp's private local Agent composition."""
    return Agent(
        InMemoryStateStore(),
        FunctionPolicyEngine(decide),
        action_executor=FunctionActionExecutor(on_action) if on_action else None,
    )
