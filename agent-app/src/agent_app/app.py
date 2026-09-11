"""Decorator-oriented local application layer built on domain-agent-core."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
import logging
from threading import Lock
from time import perf_counter
from agent_core import (
    Action,
    Agent,
    AgentResult,
    Decision,
    Event,
    State,
)
from ._local import FunctionActionExecutor, FunctionPolicyEngine, InMemoryStateStore
from .observability import AgentHealth, AgentLifecycleEvent, now


logger = logging.getLogger(__name__)


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
LifecycleObserver = Callable[[AgentLifecycleEvent], None]


class AgentApp:
    """A small, local-first composition layer for the standard Agent runtime.

    It is deliberately limited to an in-memory state store and direct local
    action dispatch. Durable state, transport, capabilities, AI reasoning and
    cross-service authorization remain explicit opt-ins through the lower-level
    packages when an application actually needs them.
    """

    def __init__(self, *, observers: Iterable[LifecycleObserver] = ()) -> None:
        self._policies: dict[str, PolicyHandler] = {}
        self._actions: dict[str, ActionHandler] = {}
        self._observers = list(observers)
        if not all(callable(observer) for observer in self._observers):
            raise TypeError("observers must be callable")
        self.state_store = InMemoryStateStore()
        self._lock = Lock()
        self._processed_events = 0
        self._succeeded_events = 0
        self._failed_events = 0

    def observe(self, observer: LifecycleObserver) -> LifecycleObserver:
        """Register a best-effort, payload-free lifecycle observer."""
        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._lock:
            self._observers.append(observer)
        return observer

    def health(self) -> AgentHealth:
        """Return local counters without performing I/O or policy evaluation."""
        with self._lock:
            return AgentHealth(
                status="degraded" if self._failed_events else "ok",
                processed_events=self._processed_events,
                succeeded_events=self._succeeded_events,
                failed_events=self._failed_events,
                registered_policies=len(self._policies),
                registered_actions=len(self._actions),
            )

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
        started_at = perf_counter()
        self._emit("started", event, occurred_at=now())
        handler = self._policies.get(event.event_type)
        try:
            if handler is None:
                raise AppConfigurationError(f"No policy is registered for event type {event.event_type!r}")
            agent = Agent(
                self.state_store,
                FunctionPolicyEngine(lambda state: self._materialize(handler(state), state)),
                action_executor=FunctionActionExecutor(self._execute),
            )
            result = agent.process(event)
        except Exception as exc:
            duration_ms = (perf_counter() - started_at) * 1_000
            with self._lock:
                self._processed_events += 1
                self._failed_events += 1
            self._emit("failed", event, occurred_at=now(), duration_ms=duration_ms, error_type=type(exc).__name__)
            raise
        duration_ms = (perf_counter() - started_at) * 1_000
        with self._lock:
            self._processed_events += 1
            self._succeeded_events += 1
        self._emit(
            "succeeded", event, occurred_at=now(), duration_ms=duration_ms,
            decision_count=len(result.decisions), action_count=len(result.actions),
        )
        return result

    run_local = process

    def _execute(self, action: Action) -> None:
        handler = self._actions.get(action.action_type)
        if handler is None:
            raise AppConfigurationError(f"No action handler is registered for {action.action_type!r}")
        handler(action)

    def _emit(
        self,
        phase: str,
        event: Event,
        *,
        occurred_at: datetime,
        duration_ms: float | None = None,
        decision_count: int | None = None,
        action_count: int | None = None,
        error_type: str | None = None,
    ) -> None:
        lifecycle = AgentLifecycleEvent(
            phase=phase, occurred_at=occurred_at, event_id=event.event_id,
            event_type=event.event_type, entity_id=event.entity_id, entity_type=event.entity_type,
            duration_ms=duration_ms, decision_count=decision_count, action_count=action_count, error_type=error_type,
        )
        logger.info(
            "agent_app.lifecycle.%s", phase,
            extra={"agent_lifecycle": lifecycle.__dict__},
        )
        with self._lock:
            observers = tuple(self._observers)
        for observer in observers:
            try:
                observer(lifecycle)
            except Exception:
                # Telemetry must never alter the policy/action outcome.
                logger.exception("agent_app.lifecycle_observer_failed")

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
