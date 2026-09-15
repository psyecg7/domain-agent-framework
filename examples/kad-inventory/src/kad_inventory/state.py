"""Domain-owned projection and guarded aggregate transition."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from .contracts import (
    CommandResult, EvaluationResult, GuardedCommand, InventoryReallocated,
    OperationalEvent, ReallocationFailed,
)


class InvariantViolationError(RuntimeError):
    """A guarded command no longer matches the live Inventory state."""

    def __init__(self, failure: ReallocationFailed) -> None:
        self.failure = failure
        super().__init__(failure.reason)


@dataclass(frozen=True)
class InventoryRecord:
    sku: str
    warehouse: str
    available_quantity: int
    version_token: int


class InMemoryInventoryState:
    """Reference projection plus atomic aggregate mutation for one process.

    Its lock makes evaluation snapshots and guarded writes coherent in this
    demonstration.  Production must replace it with the target domain's
    native conditional write (SQL OCC, ETag, etc.); a Kafka projection alone
    is not an authority to mutate physical inventory.
    """

    def __init__(self, *, evaluation_ttl_seconds: int = 30) -> None:
        if evaluation_ttl_seconds < 1:
            raise ValueError("evaluation_ttl_seconds must be positive")
        self._records: dict[tuple[str, str], InventoryRecord] = {}
        self._lock = threading.RLock()
        self._ttl = evaluation_ttl_seconds

    def seed(self, sku: str, warehouse: str, *, available_quantity: int, version_token: int = 1) -> None:
        if available_quantity < 0 or version_token < 1:
            raise ValueError("quantity must be non-negative and version_token positive")
        with self._lock:
            self._records[(sku, warehouse)] = InventoryRecord(sku, warehouse, available_quantity, version_token)

    def snapshot(self, sku: str, warehouse: str) -> InventoryRecord:
        with self._lock:
            try:
                return self._records[(sku, warehouse)]
            except KeyError as exc:
                raise ValueError(f"Unknown inventory location: {sku}/{warehouse}") from exc

    def evaluate(self, *, sku: str, warehouse: str, quantity: int) -> EvaluationResult:
        record = self.snapshot(sku, warehouse)
        return EvaluationResult(
            feasible=record.available_quantity >= quantity,
            target_warehouse=warehouse,
            evaluated_against_state_version=record.version_token,
            epistemic_status="AUTHORITATIVE_EVALUATION",
            valid_until_epoch=int(time.time()) + self._ttl,
        )

    def apply_operational_event(self, event: OperationalEvent) -> None:
        """Apply an already-authoritative operational event to the projection."""
        if event.get("event_type") not in {"InventoryStockSeeded", "CustomerInventoryConsumed"}:
            return
        sku, warehouse = event.get("sku"), event.get("warehouse")
        quantity, version = event.get("available_quantity"), event.get("version_token")
        if not isinstance(sku, str) or not isinstance(warehouse, str) or not isinstance(quantity, int) or not isinstance(version, int):
            raise ValueError("Inventory event has an invalid schema")
        self.seed(sku, warehouse, available_quantity=quantity, version_token=version)

    def execute_with_predicates(self, command: GuardedCommand) -> CommandResult:
        """Execute a command only when its signed/evaluated version remains live."""
        with self._lock:
            record = self.snapshot(command.sku, command.target_warehouse)
            if command.expected_state_version != record.version_token:
                failure = ReallocationFailed(
                    order_id=command.order_id,
                    sku=command.sku,
                    target_warehouse=command.target_warehouse,
                    expected_state_version=command.expected_state_version,
                    actual_state_version=record.version_token,
                )
                raise InvariantViolationError(failure)
            if command.quantity > record.available_quantity:
                # Availability was a predicate of the evaluation as well.
                # Use the same explicit conflict event rather than allowing a
                # stale feasibility result to mutate state.
                failure = ReallocationFailed(
                    order_id=command.order_id,
                    sku=command.sku,
                    target_warehouse=command.target_warehouse,
                    expected_state_version=command.expected_state_version,
                    actual_state_version=record.version_token,
                )
                raise InvariantViolationError(failure)
            updated = InventoryRecord(
                record.sku, record.warehouse,
                record.available_quantity - command.quantity,
                record.version_token + 1,
            )
            self._records[(updated.sku, updated.warehouse)] = updated
            return CommandResult(
                status="REALLOCATED",
                sku=updated.sku,
                target_warehouse=updated.warehouse,
                remaining_quantity=updated.available_quantity,
                state_version=updated.version_token,
            )
