from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
import uuid

from .decision import Decision


_ACTION_CONSTRUCTION_TOKEN = object()


class ActionConstructionError(TypeError):
    """Raised when Action is constructed without decision_to_action()."""


@dataclass(frozen=True, init=False)
class Action:
    action_type: str
    entity_id: str
    entity_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def __init__(
        self,
        action_type: str,
        entity_id: str,
        entity_type: str,
        parameters: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        action_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        *,
        _token: object | None = None,
    ) -> None:
        if _token is not _ACTION_CONSTRUCTION_TOKEN:
            raise ActionConstructionError(
                "Action cannot be constructed directly. Use decision_to_action(decision, ...)."
            )
        timestamp = created_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        if idempotency_key is not None and not idempotency_key.strip():
            raise ValueError("Idempotency key must not be empty")
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "entity_type", entity_type)
        object.__setattr__(self, "parameters", dict(parameters or {}))
        object.__setattr__(self, "created_at", timestamp)
        object.__setattr__(self, "action_id", action_id or str(uuid.uuid4()))
        object.__setattr__(self, "metadata", MappingProxyType(dict(metadata or {})))
        object.__setattr__(self, "idempotency_key", idempotency_key)

    def _with_metadata(self, metadata: Mapping[str, Any], *, idempotency_key: str | None = None) -> "Action":
        """Create a lineage-enriched copy without reopening public construction."""
        return Action(
            action_type=self.action_type,
            entity_id=self.entity_id,
            entity_type=self.entity_type,
            parameters=self.parameters,
            created_at=self.created_at,
            action_id=self.action_id,
            metadata=metadata,
            idempotency_key=idempotency_key if idempotency_key is not None else self.idempotency_key,
            _token=_ACTION_CONSTRUCTION_TOKEN,
        )


def decision_to_action(
    decision: Decision,
    action_type: str,
    *,
    parameters: dict[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Action:
    """Create an Action structurally tied to the target of a Decision."""
    if not isinstance(decision, Decision):
        raise ActionConstructionError("decision_to_action requires a real Decision instance.")
    return Action(
        action_type=action_type,
        entity_id=decision.entity_id,
        entity_type=decision.entity_type,
        parameters=parameters or {},
        metadata=metadata or {},
        idempotency_key=idempotency_key,
        _token=_ACTION_CONSTRUCTION_TOKEN,
    )


__all__ = ["Action", "ActionConstructionError", "decision_to_action"]
