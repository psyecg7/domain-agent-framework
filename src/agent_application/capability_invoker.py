from __future__ import annotations

from typing import Any, Mapping, Protocol
import uuid

from agent_core import Capability, Event, Intent
from agent_core.ports.capability_registry import CapabilityRegistry


class EventTransport(Protocol):
    def publish(self, event: Event) -> None:
        ...


class UnknownCapabilityError(RuntimeError):
    """Raised when an intent has no registered capability."""


class AmbiguousCapabilityError(RuntimeError):
    """Raised when an intent resolves to more than one capability."""


class AuthorizationError(PermissionError):
    """Raised before publication when a caller lacks capability permission."""


class IntentValidationError(ValueError):
    """Raised before publication when intent parameters violate a capability schema."""


class Authorizer(Protocol):
    def authorize(self, capability: Capability, context: Mapping[str, Any]) -> bool:
        ...


class CapabilityInvoker:
    """Resolve an intent and publish one capability invocation event.

    Target-agent delivery is owned by transport subscriptions. This component
    never imports or calls an Agent and never reacts recursively to results.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        transport: EventTransport,
        *,
        authorizer: Authorizer | None = None,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.authorizer = authorizer

    def invoke(
        self,
        intent: Intent,
        *,
        entity_id: str,
        entity_type: str,
        context: Mapping[str, Any] | None = None,
    ) -> Capability:
        request_context = context or {}
        matches = list(self.registry.find(intent))
        if not matches:
            raise UnknownCapabilityError(f"No capability matches {intent.intent_type}")
        if len(matches) > 1:
            raise AmbiguousCapabilityError(f"Ambiguous capabilities for {intent.intent_type}")

        capability = matches[0]
        self._validate(capability, intent)
        if self.authorizer is not None and not self.authorizer.authorize(capability, request_context):
            raise AuthorizationError(f"Not authorized for {capability.capability_id}")
        correlation_id = self._metadata_id(request_context.get("correlation_id")) or str(uuid.uuid4())
        causation_id = self._metadata_id(request_context.get("causation_id")) or str(uuid.uuid4())
        self.transport.publish(
            Event(
                event_type="capability.invocation",
                entity_id=entity_id,
                entity_type=entity_type,
                payload={
                    "capability_id": capability.capability_id,
                    "intent": {
                        "intent_type": intent.intent_type,
                        "parameters": dict(intent.parameters),
                        "metadata": dict(intent.metadata),
                        "idempotency_key": intent.idempotency_key,
                    },
                },
                source="agent",
                metadata={
                    "capability_id": capability.capability_id,
                    "owner": capability.metadata.get("owner"),
                    "correlation_id": correlation_id,
                    "causation_id": causation_id,
                },
                idempotency_key=intent.idempotency_key,
            )
        )
        return capability

    @staticmethod
    def _metadata_id(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _validate(capability: Capability, intent: Intent) -> None:
        if not capability.input_schema:
            return
        try:
            from jsonschema import ValidationError, validate
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "jsonschema is required to validate capability input_schema; "
                "install domain-agent-core[validation]"
            ) from exc
        try:
            validate(instance=dict(intent.parameters), schema=dict(capability.input_schema))
        except ValidationError as exc:
            raise IntentValidationError(exc.message) from exc


__all__ = [
    "AmbiguousCapabilityError",
    "AuthorizationError",
    "CapabilityInvoker",
    "EventTransport",
    "Authorizer",
    "IntentValidationError",
    "UnknownCapabilityError",
]
