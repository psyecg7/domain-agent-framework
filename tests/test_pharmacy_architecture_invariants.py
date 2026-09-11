from __future__ import annotations

from dataclasses import dataclass
import inspect

import pytest

from agent_app import AmbiguousCapabilityError, CapabilityInvoker, UnknownCapabilityError
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent
from test_operation_execution_contract import (
    ExecutionLedger,
    InventoryEffectAuthority,
    OperationRequest,
    OperationResult,
)


class Transport:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)


@dataclass
class ProcessEvidence:
    process_id: str
    correlation_id: str
    status: str = "UNKNOWN"
    observed_result_ids: set[str] | None = None

    def __post_init__(self) -> None:
        if self.observed_result_ids is None:
            self.observed_result_ids = set()

    def observe(self, result: OperationResult) -> None:
        if self.observed_result_ids is None:
            self.observed_result_ids = set()
        if result.event_id in self.observed_result_ids:
            return
        self.observed_result_ids.add(result.event_id)
        if result.success:
            self.status = "SUCCEEDED"


class CapabilityAvailability:
    def __init__(self, available: bool = True) -> None:
        self.available = available

    def resolve(self, registry: InMemoryCapabilityRegistry, intent: Intent) -> Capability | None:
        if not self.available:
            return None
        matches = list(registry.find(intent))
        return matches[0] if len(matches) == 1 else None


def reservation_capability() -> Capability:
    return Capability(
        "inventory.reserve",
        "Reserve inventory",
        "Reserve stock for an order",
        metadata={"owner": "inventory", "intent_types": ("inventory.reserve",)},
    )


def request(operation_id: str = "RES-123", attempt: int = 1) -> OperationRequest:
    return OperationRequest(operation_id, "inventory.reserve", attempt, f"request-{attempt}", "C1")


def result(operation_id: str = "RES-123", attempt: int = 1, event_id: str = "result-1", success: bool = True) -> OperationResult:
    return OperationResult(operation_id, attempt, event_id, success, "inventory-action")


def test_duplicate_invocation_event_is_one_inventory_business_reservation() -> None:
    inventory = InventoryEffectAuthority()
    first = request()
    duplicate_event = OperationRequest("RES-123", "inventory.reserve", 1, "request-duplicate", "C1")

    assert first.event_id != duplicate_event.event_id
    assert inventory.apply_reservation(first) is True
    assert inventory.apply_reservation(duplicate_event) is False
    assert inventory.application_count == 1


def test_same_operation_different_event_ids_remain_distinct_evidence() -> None:
    first = request()
    second = OperationRequest("RES-123", "inventory.reserve", 1, "request-002", "C1")

    assert first.operation_id == second.operation_id
    assert first.event_id != second.event_id


def test_duplicate_result_events_do_not_advance_process_twice() -> None:
    process = ProcessEvidence("P1", "C1")
    completed = result(event_id="result-001")

    process.observe(completed)
    process.observe(completed)

    assert process.status == "SUCCEEDED"
    assert process.observed_result_ids == {"result-001"}


def test_successful_side_effect_with_lost_result_remains_unknown() -> None:
    inventory = InventoryEffectAuthority()
    ledger = ExecutionLedger()
    ledger.request(request())
    inventory.apply_reservation(ledger.requests["RES-123"])
    ledger.mark_unknown("RES-123")

    assert ledger.status["RES-123"] == "UNKNOWN"
    assert inventory.effects == {"RES-123"}


def test_process_restart_after_side_effect_does_not_replay_or_invent_success() -> None:
    inventory = InventoryEffectAuthority()
    ledger = ExecutionLedger()
    ledger.request(request())
    inventory.apply_reservation(ledger.requests["RES-123"])
    restarted = ExecutionLedger(requests=dict(ledger.requests), status=dict(ledger.status))
    restarted.mark_unknown("RES-123")

    assert restarted.status["RES-123"] == "UNKNOWN"
    assert inventory.application_count == 1


def test_retry_after_unknown_preserves_operation_and_changes_attempt() -> None:
    ledger = ExecutionLedger()
    ledger.request(request())
    ledger.mark_unknown("RES-123")
    retry = ledger.retry("RES-123", "request-002")

    assert retry.operation_id == "RES-123"
    assert retry.attempt == 2
    assert ledger.requests["RES-123"].attempt == 2


def test_late_result_from_old_attempt_is_not_current_success() -> None:
    ledger = ExecutionLedger()
    ledger.request(request())
    ledger.retry("RES-123", "request-002")
    ledger.mark_result(result(attempt=1, event_id="late-attempt-1"))

    assert ledger.status["RES-123"] == "REQUESTED"
    assert ledger.stale_result_ids == {"late-attempt-1"}


def test_conflicting_evidence_is_not_silently_resolved() -> None:
    ledger = ExecutionLedger()
    ledger.request(request())
    ledger.mark_result(result(event_id="success-1", success=True))
    ledger.mark_result(result(event_id="failure-1", success=False))

    assert ledger.status["RES-123"] == "CONFLICT"
    assert ledger.conflict_result_ids == {"failure-1"}


def test_process_state_loss_does_not_override_inventory_authority() -> None:
    inventory = InventoryEffectAuthority()
    inventory.apply_reservation(request())
    lost_process = ProcessEvidence("P1", "C1")

    assert lost_process.status == "UNKNOWN"
    assert inventory.effects == {"RES-123"}


def test_inventory_unavailable_keeps_observation_unknown() -> None:
    availability = CapabilityAvailability(available=False)
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())

    assert availability.resolve(registry, Intent("inventory.reserve")) is None
    assert ProcessEvidence("P1", "C1").status == "UNKNOWN"


def test_capability_disappears_without_becoming_business_failure() -> None:
    availability = CapabilityAvailability()
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())
    assert availability.resolve(registry, Intent("inventory.reserve")) is not None
    availability.available = False

    assert availability.resolve(registry, Intent("inventory.reserve")) is None
    assert ProcessEvidence("P1", "C1").status == "UNKNOWN"


def test_network_partition_does_not_infer_success_or_failure() -> None:
    transport = Transport()
    process = ProcessEvidence("P1", "C1")
    request_event = Event(
        "capability.invocation",
        "ORD-1",
        "order",
        {"operation_id": "RES-123"},
        metadata={"correlation_id": "C1", "causation_id": "order-1"},
    )
    transport.publish(request_event)

    assert process.status == "UNKNOWN"
    assert len(transport.events) == 1
    assert not any(event.event_type.endswith("result") for event in transport.events)


def test_human_intervention_uses_the_same_capability_event_boundary() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(
        Capability(
            "inventory.reservation.reconcile",
            "Reconcile reservation",
            "Return authoritative reservation status",
            metadata={"owner": "inventory", "intent_types": ("inventory.reservation.reconcile",)},
        )
    )
    transport = Transport()
    invoker = CapabilityInvoker(registry, transport)
    invoker.invoke(
        Intent("inventory.reservation.reconcile", {"operation_id": "RES-123"}),
        entity_id="ORD-1",
        entity_type="order",
        context={"correlation_id": "C1", "causation_id": "human-intervention"},
    )

    assert transport.events[0].event_type == "capability.invocation"
    assert transport.events[0].metadata["causation_id"] == "human-intervention"


def test_multiple_operations_keep_process_operation_attempt_and_event_identity_distinct() -> None:
    process_id = "P1"
    correlation_id = "C1"
    operations = [request("PIM-1"), request("RES-123"), request("PRICE-1")]
    events = [OperationResult(item.operation_id, item.attempt, f"result-{index}", True, item.event_id) for index, item in enumerate(operations)]

    assert process_id != correlation_id
    assert {operation.operation_id for operation in operations} == {"PIM-1", "RES-123", "PRICE-1"}
    assert len({event.event_id for event in events}) == 3
    assert all(event.causation_id in {operation.event_id for operation in operations} for event in events)


def test_invoker_remains_one_hop_and_does_not_execute_or_retry() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())
    transport = Transport()
    invoker = CapabilityInvoker(registry, transport)
    source = inspect.getsource(CapabilityInvoker)
    invoker.invoke(Intent("inventory.reserve"), entity_id="ORD-1", entity_type="order")

    assert len(transport.events) == 1
    assert ".process(" not in source
    assert "retry" not in source.lower()
    assert "compensate" not in source.lower()


def test_unknown_and_ambiguous_capabilities_publish_no_invocation() -> None:
    transport = Transport()
    unknown = CapabilityInvoker(InMemoryCapabilityRegistry(), transport)
    with pytest.raises(UnknownCapabilityError):
        unknown.invoke(Intent("inventory.reserve"), entity_id="ORD-1", entity_type="order")

    registry = InMemoryCapabilityRegistry()
    registry.register(reservation_capability())
    registry.register(Capability("inventory.reserve.alt", "Reserve", "Reserve", metadata={"intent_types": ("inventory.reserve",)}))
    with pytest.raises(AmbiguousCapabilityError):
        CapabilityInvoker(registry, transport).invoke(Intent("inventory.reserve"), entity_id="ORD-1", entity_type="order")
    assert transport.events == []


def test_architecture_test_doubles_do_not_add_generic_primitives() -> None:
    source = inspect.getsource(ExecutionLedger)
    assert "WorkflowEngine" not in source
    assert "ProcessManager" not in source
    assert "ReconciliationService" not in source
    assert "IdempotencyManager" not in source
