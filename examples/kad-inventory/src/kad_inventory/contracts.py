"""Typed wire contracts for the knowledge-aware Inventory reference."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class EvaluateRequest(BaseModel):
    sku: str = Field(min_length=1)
    target_warehouse: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class EvaluationResult(BaseModel):
    feasible: bool
    target_warehouse: str
    evaluated_against_state_version: int
    epistemic_status: Literal["AUTHORITATIVE_EVALUATION"]
    valid_until_epoch: int


class MoveOrderIntent(BaseModel):
    """An untrusted proposed move; the domain validates it before execution."""

    order_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    target_warehouse: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    expected_state_version: int = Field(ge=1)


class GuardedCommand(BaseModel):
    """The domain command compiled from an intent plus its required predicate."""

    order_id: str
    sku: str
    target_warehouse: str
    quantity: int
    expected_state_version: int


class CommandResult(BaseModel):
    status: Literal["REALLOCATED"]
    sku: str
    target_warehouse: str
    remaining_quantity: int
    state_version: int


class ReallocationFailed(BaseModel):
    event_type: Literal["ReallocationFailed"] = "ReallocationFailed"
    reason: Literal["INVARIANT_VIOLATION_VERSION_MISMATCH"] = "INVARIANT_VIOLATION_VERSION_MISMATCH"
    order_id: str
    sku: str
    target_warehouse: str
    expected_state_version: int
    actual_state_version: int


class InventoryReallocated(BaseModel):
    event_type: Literal["InventoryReallocated"] = "InventoryReallocated"
    order_id: str
    sku: str
    target_warehouse: str
    quantity: int
    state_version: int


class MessageMetadata(BaseModel):
    """Lineage that survives topic hops, redelivery, and process restart."""

    correlation_id: str = Field(min_length=1)
    causation_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)


class KafkaMessage(BaseModel):
    """A versioned domain message; topics route it but do not identify it."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: Literal[
        "InventoryReallocationRequested", "InventoryReallocated", "ReallocationFailed",
    ]
    entity_id: str = Field(min_length=1)
    entity_type: Literal["order"] = "order"
    payload: dict[str, Any]
    metadata: MessageMetadata


OperationalEvent = dict[str, Any]
