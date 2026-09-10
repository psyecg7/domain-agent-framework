"""Decorator-oriented local application layer built on domain-agent-core."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from agent_core import (
    Action,
    Agent,
    AgentResult,
    Decision,
    Event,
    State,
)
from ._local import FunctionActionExecutor, FunctionPolicyEngine, InMemoryStateStore


class AppConfigurationError(ValueError):
    """The small application API was configured ambiguously or incompletely."""


@dataclass(frozen=True)
class DecisionSpec:
    """A policy's target-independent decision declaration.

    AgentApp materializes this only while evaluating a concrete entity state,
    so application policy does not need to repeat entity identifiers.
    """

    decision_type: str
    severity: str
    reason: str

    def for_state(self, state: State) -> Decision:
        return Decision(
            state.entity_id,
            state.entity_type,
            self.decision_type,
            self.severity,
            self.reason,
        )


PolicyHandler = Callable[[State], DecisionSpec | Decision | Iterable[DecisionSpec | Decision] | None]
ActionHandler = Callable[[Action], None]


class AgentApp:
    """A small, local-first composition layer for the standard Agent runtime.

    It is deliberately limited to an in-memory state store and direct local
    action dispatch. Durable state, transport, capabilities, AI reasoning and
    cross-service authorization remain explicit opt-ins through the lower-level
    packages when an application actually needs them.
    """

    def __init__(self) -> None:
        self._policies: dict[str, PolicyHandler] = {}
        self._actions: dict[str, ActionHandler] = {}
        self.state_store = InMemoryStateStore()

    def policy(self, event_type: str) -> Callable[[PolicyHandler], PolicyHandler]:
        """Register the deterministic policy for one incoming event type."""
        if not isinstance(event_type, str) or not event_type.strip():
            raise AppConfigurationError("event_type must be a non-empty string")

        def register(handler: PolicyHandler) -> PolicyHandler:
            if event_type in self._policies:
                raise AppConfigurationError(f"A policy is already registered for {event_type!r}")
            self._policies[event_type] = handler
            return handler

        return register

    def action(self, action_type: str) -> Callable[[ActionHandler], ActionHandler]:
        """Register the local handler for one already-authorized action type."""
        if not isinstance(action_type, str) or not action_type.strip():
            raise AppConfigurationError("action_type must be a non-empty string")

        def register(handler: ActionHandler) -> ActionHandler:
            if action_type in self._actions:
                raise AppConfigurationError(f"An action handler is already registered for {action_type!r}")
            self._actions[action_type] = handler
            return handler

        return register

    @staticmethod
    def decide(decision_type: str, *, severity: str = "MEDIUM", reason: str = "Policy approved") -> DecisionSpec:
        """Declare a Decision without repeating the current entity target."""
        if not all(isinstance(value, str) and value.strip() for value in (decision_type, severity, reason)):
            raise AppConfigurationError("decision_type, severity, and reason must be non-empty strings")
        return DecisionSpec(decision_type, severity, reason)

    def process(self, event: Event) -> AgentResult:
        """Apply an Event using its registered policy and local action handlers."""
        handler = self._policies.get(event.event_type)
        if handler is None:
            raise AppConfigurationError(f"No policy is registered for event type {event.event_type!r}")
        agent = Agent(
            self.state_store,
            FunctionPolicyEngine(lambda state: self._materialize(handler(state), state)),
            action_executor=FunctionActionExecutor(self._execute),
        )
        return agent.process(event)

    run_local = process

    def _execute(self, action: Action) -> None:
        handler = self._actions.get(action.action_type)
        if handler is None:
            raise AppConfigurationError(f"No action handler is registered for {action.action_type!r}")
        handler(action)

    @staticmethod
    def _materialize(value: DecisionSpec | Decision | Iterable[DecisionSpec | Decision] | None, state: State) -> list[Decision]:
        if value is None:
            return []
        values: Iterable[DecisionSpec | Decision] = (value,) if isinstance(value, (DecisionSpec, Decision)) else value
        decisions: list[Decision] = []
        for item in values:
            if isinstance(item, DecisionSpec):
                decisions.append(item.for_state(state))
            elif isinstance(item, Decision):
                decisions.append(item)
            else:
                raise TypeError("An AgentApp policy must return Decision, DecisionSpec, an iterable, or None")
        return decisions
