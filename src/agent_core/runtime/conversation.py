from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol
import uuid

from agent_core.primitives.capability import Capability
from agent_core.primitives.event import Event
from agent_core.ports.capability_registry import CapabilityRegistry
from agent_core.ports.intent_interpreter import IntentInterpreter
from .response import ResponseInterpretationError, ResponseInterpreter


class EventTransport(Protocol):
    def publish(self, event: Event) -> None:
        ...


class EventSubscriber(Protocol):
    def subscribe(self, event_type: str, handler: Any) -> None:
        ...


@dataclass(frozen=True)
class ConversationResponse:
    text: str
    correlation_id: str
    causation_id: str
    event: Event


class CapabilityResolutionError(RuntimeError):
    """Raised when an intent cannot be resolved to exactly one capability."""


class ConversationalGateway:
    """Translate a request into one capability-routed invocation event."""

    def __init__(
        self,
        interpreter: IntentInterpreter,
        registry: CapabilityRegistry,
        transport: EventTransport,
        response_interpreter: ResponseInterpreter | None = None,
    ) -> None:
        self.interpreter = interpreter
        self.registry = registry
        self.transport = transport
        self.response_interpreter = response_interpreter
        self._pending: dict[str, str] = {}
        self.responses: list[ConversationResponse] = []
        self._subscribed_result_types: set[str] = set()

    def handle(
        self,
        request: str,
        *,
        entity_id: str,
        entity_type: str,
        context: Mapping[str, Any] | None = None,
    ) -> Capability:
        request_context = context or {}
        intent = self.interpreter.interpret(request, request_context)
        matches = list(self.registry.find(intent))
        if not matches:
            raise CapabilityResolutionError(f"No capability matches {intent.intent_type}")
        if len(matches) > 1:
            raise CapabilityResolutionError(f"Ambiguous capabilities for {intent.intent_type}")

        capability = matches[0]
        correlation_id = self._metadata_id(request_context.get("correlation_id")) or str(uuid.uuid4())
        request_id = self._metadata_id(request_context.get("request_id")) or str(uuid.uuid4())
        result_event_type = capability.metadata.get("result_event_type")
        if result_event_type is not None and (
            not isinstance(result_event_type, str) or not result_event_type.strip()
        ):
            raise CapabilityResolutionError("Capability has an invalid result event type")
        if isinstance(result_event_type, str):
            self._pending[correlation_id] = result_event_type
            self._subscribe_to_result(result_event_type)

        invocation = Event(
            event_type="intent.requested",
            entity_id=entity_id,
            entity_type=entity_type,
            payload={"intent_type": intent.intent_type, **intent.parameters},
            source="conversation",
            metadata={
                "capability_id": capability.capability_id,
                "correlation_id": correlation_id,
                "causation_id": request_id,
            },
        )
        self.transport.publish(invocation)
        return capability

    def handle_result(
        self,
        event: Event,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> ConversationResponse | None:
        if self.response_interpreter is None:
            return None
        if not isinstance(event.event_type, str) or not event.event_type.strip():
            return None
        if not isinstance(event.payload, dict) or not isinstance(event.metadata, dict):
            return None

        correlation_id = self._metadata_id(event.metadata.get("correlation_id"))
        if correlation_id is None:
            return None
        expected_type = self._pending.get(correlation_id)
        if expected_type != event.event_type:
            return None

        causation_id = self._metadata_id(event.metadata.get("causation_id"))
        if causation_id is None:
            return None

        try:
            text = self.response_interpreter.interpret(event, context or {})
        except (KeyError, ResponseInterpretationError, ValueError, TypeError):
            return None

        response = ConversationResponse(text, correlation_id, causation_id, event)
        self.responses.append(response)
        del self._pending[correlation_id]
        return response

    def _subscribe_to_result(self, event_type: str) -> None:
        if event_type in self._subscribed_result_types:
            return
        subscribe = getattr(self.transport, "subscribe", None)
        if callable(subscribe):
            subscribe(event_type, self.handle_result)
            self._subscribed_result_types.add(event_type)

    @staticmethod
    def _metadata_id(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None


__all__ = [
    "CapabilityResolutionError",
    "ConversationResponse",
    "ConversationalGateway",
    "EventSubscriber",
    "EventTransport",
]