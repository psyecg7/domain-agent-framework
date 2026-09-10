from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import inspect

from agent_core import Event


@dataclass
class ProcessSnapshot:
    process_id: str
    order_id: str
    product_id: str
    correlation_id: str
    status: str
    facts: dict[str, str] = field(default_factory=dict)
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    applied_event_ids: set[str] = field(default_factory=set)
    recovery_reason: str | None = None


class ProcessStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, ProcessSnapshot] = {}

    def save(self, process: "PurchaseProcess") -> None:
        self.snapshots[process.process_id] = process.snapshot()

    def load(self, process_id: str) -> ProcessSnapshot:
        snapshot = self.snapshots[process_id]
        return ProcessSnapshot(
            process_id=snapshot.process_id,
            order_id=snapshot.order_id,
            product_id=snapshot.product_id,
            correlation_id=snapshot.correlation_id,
            status=snapshot.status,
            facts=dict(snapshot.facts),
            operations={key: dict(value) for key, value in snapshot.operations.items()},
            applied_event_ids=set(snapshot.applied_event_ids),
            recovery_reason=snapshot.recovery_reason,
        )


class PurchaseProcess:
    """Order-owned process semantics for one purchase attempt, not a framework engine."""

    FACT_DOMAINS = ("pim", "inventory", "pricing")
    OPERATION_KINDS = ("order.create", "inventory.reserve")

    def __init__(
        self,
        process_id: str,
        order_id: str,
        product_id: str,
        correlation_id: str,
        *,
        snapshot: ProcessSnapshot | None = None,
    ) -> None:
        self.process_id = process_id
        self.order_id = order_id
        self.product_id = product_id
        self.correlation_id = correlation_id
        self.status = "PENDING_FACTS"
        self.facts: dict[str, str] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.applied_event_ids: set[str] = set()
        self.recovery_reason: str | None = None
        if snapshot is not None:
            self._restore(snapshot)

    def observe(self, event: Event) -> None:
        if event.metadata.get("correlation_id") != self.correlation_id:
            return
        if event.event_id in self.applied_event_ids:
            return
        self.applied_event_ids.add(event.event_id)
        if event.event_type in {"pim.result", "inventory.availability.result", "pricing.result"}:
            domain = event.payload.get("domain")
            if domain in self.FACT_DOMAINS:
                self.facts.setdefault(domain, "VALID" if event.payload.get("valid") is True else "DENIED")
            if len(self.facts) == len(self.FACT_DOMAINS):
                if all(value == "VALID" for value in self.facts.values()):
                    self.status = "READY"
                else:
                    self.status = "FAILED"
            return

        operation_id = event.payload.get("operation_id")
        if event.event_type in {"order.created.result", "inventory.reservation.result"} and isinstance(operation_id, str):
            operation = self.operations.setdefault(
                operation_id,
                {"kind": event.payload.get("operation_kind"), "status": "PENDING", "attempts": 0},
            )
            if operation["status"] in {"SUCCEEDED", "FAILED"}:
                return
            operation["status"] = "SUCCEEDED" if event.payload.get("success") is True else "FAILED"
            operation["last_event_id"] = event.event_id
            operation["last_causation_id"] = event.metadata.get("causation_id")
            self._update_operation_status()

    def begin_execution(self) -> None:
        if self.status != "READY":
            raise ValueError("Purchase is not ready for execution")
        self.status = "EXECUTING"
        for kind in self.OPERATION_KINDS:
            operation_id = f"{self.process_id}:{kind}"
            self.operations.setdefault(
                operation_id,
                {"kind": kind, "status": "PENDING", "attempts": 0},
            )

    def record_operation_request(self, operation_id: str, kind: str) -> bool:
        if kind not in self.OPERATION_KINDS:
            raise ValueError(f"Unsupported operation: {kind}")
        if operation_id in self.operations:
            return False
        self.operations[operation_id] = {"kind": kind, "status": "PENDING", "attempts": 1}
        return True

    def record_temporary_failure(self, operation_id: str, event_id: str) -> None:
        operation = self.operations[operation_id]
        operation.update({"status": "TEMPORARY_FAILURE", "last_event_id": event_id})
        if self.status == "READY":
            self.status = "EXECUTING"

    def request_retry(self, operation_id: str) -> None:
        operation = self.operations[operation_id]
        if operation["status"] != "TEMPORARY_FAILURE":
            raise ValueError("Only temporary failures may be retried")
        operation.update({"status": "PENDING", "attempts": operation["attempts"] + 1})

    def mark_timeout(self, operation_id: str) -> None:
        operation = self.operations[operation_id]
        operation.update({"status": "TIMED_OUT"})
        self.status = "RECOVERY_REQUIRED"
        self.recovery_reason = f"Operation timed out: {operation_id}"

    def request_recovery(self, reason: str) -> None:
        if self.status not in {"PARTIALLY_COMPLETED", "RECOVERY_REQUIRED", "FAILED"}:
            raise ValueError("Recovery is not required")
        self.status = "RECOVERY_REQUIRED"
        self.recovery_reason = reason

    def snapshot(self) -> ProcessSnapshot:
        return ProcessSnapshot(
            process_id=self.process_id,
            order_id=self.order_id,
            product_id=self.product_id,
            correlation_id=self.correlation_id,
            status=self.status,
            facts=dict(self.facts),
            operations={key: dict(value) for key, value in self.operations.items()},
            applied_event_ids=set(self.applied_event_ids),
            recovery_reason=self.recovery_reason,
        )

    def _restore(self, snapshot: ProcessSnapshot) -> None:
        if snapshot.process_id != self.process_id or snapshot.correlation_id != self.correlation_id:
            raise ValueError("Snapshot identity does not match process")
        self.status = snapshot.status
        self.facts = dict(snapshot.facts)
        self.operations = {key: dict(value) for key, value in snapshot.operations.items()}
        self.applied_event_ids = set(snapshot.applied_event_ids)
        self.recovery_reason = snapshot.recovery_reason

    def _update_operation_status(self) -> None:
        statuses = [operation["status"] for operation in self.operations.values()]
        if len(statuses) < len(self.OPERATION_KINDS):
            return
        if all(status == "SUCCEEDED" for status in statuses):
            self.status = "COMPLETED"
        elif any(status == "SUCCEEDED" for status in statuses):
            self.status = "PARTIALLY_COMPLETED"


def result_event(
    event_type: str,
    correlation_id: str,
    payload: dict[str, Any],
    *,
    causation_id: str = "action-1",
) -> Event:
    return Event(
        event_type=event_type,
        entity_id="ORD-1",
        entity_type="order",
        payload=payload,
        source="domain",
        metadata={"correlation_id": correlation_id, "causation_id": causation_id},
    )


def valid_fact_events(correlation_id: str) -> list[Event]:
    return [
        result_event("pricing.result", correlation_id, {"domain": "pricing", "valid": True}),
        result_event("inventory.availability.result", correlation_id, {"domain": "inventory", "valid": True}),
        result_event("pim.result", correlation_id, {"domain": "pim", "valid": True}),
    ]


def make_process(correlation_id: str = "buy-1") -> PurchaseProcess:
    return PurchaseProcess("process-1", "ORD-1", "P123", correlation_id)


def prepare_execution(process: PurchaseProcess) -> None:
    for event in valid_fact_events(process.correlation_id):
        process.observe(event)
    process.begin_execution()


def test_process_identity_is_distinct_from_event_lineage() -> None:
    process = make_process("buy-1")
    event = valid_fact_events("buy-1")[0]

    assert process.process_id != event.event_id
    assert process.process_id != event.metadata["causation_id"]
    assert process.correlation_id == event.metadata["correlation_id"]
    assert process.order_id == "ORD-1"


def test_process_lifecycle_and_out_of_order_facts() -> None:
    process = make_process()

    for event in valid_fact_events("buy-1"):
        process.observe(event)
    assert process.status == "READY"
    process.begin_execution()
    assert process.status == "EXECUTING"
    assert set(process.operations) == {"process-1:order.create", "process-1:inventory.reserve"}

    process.observe(result_event("inventory.reservation.result", "buy-1", {"operation_id": "process-1:inventory.reserve", "operation_kind": "inventory.reserve", "success": True}))
    process.observe(result_event("order.created.result", "buy-1", {"operation_id": "process-1:order.create", "operation_kind": "order.create", "success": True}))
    assert process.status == "COMPLETED"


def test_process_does_not_duplicate_domain_state() -> None:
    process = make_process()
    process.observe(result_event("inventory.availability.result", "buy-1", {"domain": "inventory", "valid": True, "available_stock": 17}))

    assert process.facts == {"inventory": "VALID"}
    assert "available_stock" not in process.snapshot().__dict__
    assert "inventory_stock" not in process.snapshot().__dict__


def test_partial_completion_requires_explicit_recovery() -> None:
    process = make_process()
    prepare_execution(process)
    reservation = result_event("inventory.reservation.result", "buy-1", {"operation_id": "process-1:inventory.reserve", "operation_kind": "inventory.reserve", "success": True})
    order_failure = result_event("order.created.result", "buy-1", {"operation_id": "process-1:order.create", "operation_kind": "order.create", "success": False})
    process.observe(reservation)
    process.observe(order_failure)

    assert process.status == "PARTIALLY_COMPLETED"
    assert process.operations["process-1:inventory.reserve"]["status"] == "SUCCEEDED"
    assert process.recovery_reason is None
    process.request_recovery("Order failed after reservation")
    assert process.status == "RECOVERY_REQUIRED"
    assert process.recovery_reason == "Order failed after reservation"


def test_missing_result_and_timeout_are_distinct_from_failure() -> None:
    process = make_process()
    prepare_execution(process)
    process.mark_timeout("process-1:inventory.reserve")

    assert process.status == "RECOVERY_REQUIRED"
    assert process.operations["process-1:inventory.reserve"]["status"] == "TIMED_OUT"
    assert process.recovery_reason == "Operation timed out: process-1:inventory.reserve"


def test_temporary_failure_requires_explicit_retry() -> None:
    process = make_process()
    prepare_execution(process)
    operation_id = "process-1:inventory.reserve"
    process.record_temporary_failure(operation_id, "temporary-event")

    assert process.status == "EXECUTING"
    assert process.operations[operation_id]["status"] == "TEMPORARY_FAILURE"
    assert process.operations[operation_id]["attempts"] == 0
    process.request_retry(operation_id)
    assert process.operations[operation_id]["status"] == "PENDING"
    assert process.operations[operation_id]["attempts"] == 1


def test_duplicate_operation_identity_does_not_create_second_logical_operation() -> None:
    process = make_process()
    prepare_execution(process)
    operation_id = "process-1:inventory.reserve"

    assert process.record_operation_request(operation_id, "inventory.reserve") is False
    assert list(process.operations).count(operation_id) == 1


def test_duplicate_event_is_ignored_and_late_event_cannot_reopen_process() -> None:
    process = make_process()
    prepare_execution(process)
    reservation = result_event("inventory.reservation.result", "buy-1", {"operation_id": "process-1:inventory.reserve", "operation_kind": "inventory.reserve", "success": True})
    order = result_event("order.created.result", "buy-1", {"operation_id": "process-1:order.create", "operation_kind": "order.create", "success": True})
    process.observe(reservation)
    process.observe(order)
    process.observe(reservation)
    assert process.status == "COMPLETED"
    assert len(process.applied_event_ids) == 5


def test_process_snapshot_can_survive_restart_and_replay() -> None:
    store = ProcessStore()
    process = make_process()
    facts = valid_fact_events("buy-1")
    process.observe(facts[0])
    store.save(process)

    restarted = PurchaseProcess("process-1", "ORD-1", "P123", "buy-1", snapshot=store.load("process-1"))
    restarted.observe(facts[0])
    restarted.observe(facts[1])
    restarted.observe(facts[2])

    assert restarted.status == "READY"
    assert restarted.facts == {"pricing": "VALID", "inventory": "VALID", "pim": "VALID"}
    assert len(restarted.applied_event_ids) == 3


def test_lineage_is_preserved_in_process_observations() -> None:
    process = make_process("lineage-1")
    event = result_event(
        "inventory.reservation.result",
        "lineage-1",
        {"operation_id": "process-1:inventory.reserve", "operation_kind": "inventory.reserve", "success": True},
        causation_id="reservation-action-1",
    )
    process.begin_execution = lambda: None
    process.operations["process-1:inventory.reserve"] = {"kind": "inventory.reserve", "status": "PENDING", "attempts": 1}
    process.observe(event)

    operation = process.operations["process-1:inventory.reserve"]
    assert operation["last_event_id"] == event.event_id
    assert operation["last_causation_id"] == "reservation-action-1"
    assert process.correlation_id == event.metadata["correlation_id"]


def test_process_has_no_agent_or_domain_store_dependency() -> None:
    source = inspect.getsource(PurchaseProcess)
    assert "Agent" not in source
    assert "StateStore" not in source
    assert ".process(" not in source
    assert "inventory_agent" not in source
    assert "pricing_agent" not in source
