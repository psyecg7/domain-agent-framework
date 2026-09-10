from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable

from agent_core.primitives.action import Action, decision_to_action
from agent_core.primitives.capability import Capability
from agent_core.primitives.decision import Decision
from agent_core.primitives.event import Event
from agent_core.primitives.observation import Observation
from agent_core.primitives.recommendation import Recommendation
from agent_core.primitives.reasoning_context import ReasoningContext
from agent_core.primitives.state import State
from agent_core.ports.action_executor import ActionExecutor
from agent_core.ports.memory_store import MemoryStore
from agent_core.ports.policy_engine import PolicyEngine
from agent_core.ports.reasoner import Reasoner
from agent_core.ports.state_store import StateStore


@dataclass(frozen=True)
class AgentResult:
    event: Event
    observation: Observation
    state: State
    decisions: list[Decision]
    actions: list[Action]
    recommendations: list[Recommendation]


ActionFactory = Callable[[Decision], Action]


class Agent:
    def __init__(
        self,
        state_store: StateStore,
        policy_engine: PolicyEngine,
        reasoner: Reasoner | None = None,
        action_executor: ActionExecutor | None = None,
        action_factory: ActionFactory | None = None,
        memory_store: MemoryStore | None = None,
        memory_limit: int = 5,
        capabilities: tuple[Capability, ...] = (),
    ) -> None:
        self.state_store = state_store
        self.policy_engine = policy_engine
        self.reasoner = reasoner
        self.action_executor = action_executor
        self.action_factory = action_factory or self._default_action_factory
        self.memory_store = memory_store
        self.memory_limit = memory_limit
        self._capabilities = tuple(capabilities)

    def capabilities(self) -> tuple[Capability, ...]:
        """Return the agent's declarative public capability contract."""
        return self._capabilities

    def process(self, event: Event) -> AgentResult:
        observations = self._event_to_observations(event)
        if not observations:
            raise ValueError("No observations produced from event")

        state = self.state_store.get(event.entity_id, event.entity_type)
        if state is None:
            state = State(entity_id=event.entity_id, entity_type=event.entity_type)

        for observation in observations:
            state.apply_observation(observation)

        self.state_store.save(state)

        recommendations: list[Recommendation] = []
        if self.reasoner is not None and self._uses_context_reasoner():
            decisions, recommendations = self._reason(state, observations, [], event)
        else:
            decisions = self.policy_engine.evaluate(state)
            if self.reasoner is not None:
                decisions, recommendations = self._reason(state, observations, decisions, event)

        actions = []
        for decision in decisions:
            action = self._add_event_lineage(self.action_factory(decision), event)
            actions.append(action)
        if self.action_executor is not None:
            for action in actions:
                self.action_executor.execute(action)

        return AgentResult(
            event=event,
            observation=observations[0],
            state=state,
            decisions=decisions,
            actions=actions,
            recommendations=recommendations,
        )

    def _add_event_lineage(self, action: Action, event: Event) -> Action:
        if not isinstance(action, Action):
            return action
        metadata = dict(action.metadata)
        correlation_id = event.metadata.get("correlation_id")
        if isinstance(correlation_id, str) and correlation_id:
            metadata["correlation_id"] = correlation_id
        metadata["causation_id"] = event.event_id
        idempotency_key = action.idempotency_key or event.idempotency_key
        return action._with_metadata(metadata, idempotency_key=idempotency_key)

    def _uses_context_reasoner(self) -> bool:
        return len(inspect.signature(self.reasoner.reason).parameters) == 1

    def _reason(
        self,
        state: State,
        observations: list[Observation],
        existing_decisions: list[Decision],
        event: Event,
    ) -> tuple[list[Decision], list[Recommendation]]:
        reason = self.reasoner.reason
        parameter_count = len(inspect.signature(reason).parameters)
        if parameter_count != 1:
            return reason(state, existing_decisions), []

        memories = []
        if self.memory_store is not None:
            memories = self.memory_store.search(
                event.event_type,
                limit=self.memory_limit,
                entity_id=event.entity_id,
                entity_type=event.entity_type,
            )
        context = ReasoningContext(
            state=state,
            observations=observations,
            memories=memories,
            metadata={"event_type": event.event_type, "event_id": event.event_id},
        )
        recommendations = list(reason(context))
        evaluate_recommendations = getattr(self.policy_engine, "evaluate_recommendations", None)
        if evaluate_recommendations is None:
            return self.policy_engine.evaluate(state), recommendations
        return evaluate_recommendations(state, recommendations), recommendations

    def _event_to_observations(self, event: Event) -> list[Observation]:
        payload = event.payload or {}
        if not isinstance(payload, dict):
            return [
                Observation(
                    entity_id=event.entity_id,
                    entity_type=event.entity_type,
                    name="payload",
                    value=payload,
                    source=event.source,
                    metadata={"event_type": event.event_type, "event_id": event.event_id},
                )
            ]

        if not payload:
            return [
                Observation(
                    entity_id=event.entity_id,
                    entity_type=event.entity_type,
                    name="event",
                    value={"event_type": event.event_type},
                    source=event.source,
                    metadata={"event_type": event.event_type, "event_id": event.event_id},
                )
            ]

        observations: list[Observation] = []
        for name, value in payload.items():
            observations.append(
                Observation(
                    entity_id=event.entity_id,
                    entity_type=event.entity_type,
                    name=name,
                    value=value,
                    source=event.source,
                    metadata={"event_type": event.event_type, "event_id": event.event_id},
                )
            )
        return observations

    def _default_action_factory(self, decision: Decision) -> Action:
        parameters: dict[str, Any] = {
            "decision_id": decision.decision_id,
            "reason": decision.reason,
            "severity": decision.severity,
        }
        return decision_to_action(decision, decision.decision_type, parameters=parameters)
