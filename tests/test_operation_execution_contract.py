from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import inspect

from agent_app import CapabilityInvoker
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent


@dataclass(frozen=True)
class OperationRequest:
    operation_id: str
    operation_kind: str
    attempt: int
    event_id: str
    correlation_id: str


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    attempt: int
    event_id: str
    success: bool
    causation_id: str


@dataclass
class ExecutionLedger:
    requests: dict[str, OperationRequest] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    observed_results: dict[str, OperationResult] = field(default_factory=dict)
    applied_result_ids: set[str] = field(default_factory=set)
    stale_result_ids: set[str] = field(default_factory=set)
    conflict_result_ids: set[str] = field(default_factory=set)

    def request(self, operation: OperationRequest) -> None:
        self.requests[operation.operation_id] = operation
        self.status.setdefault(operation.operation_id, "REQUESTED")

    def mark_result(self, result: OperationResult) -> None:
        if result.event_id in self.applied_result_ids:
            return
        self.applied_result_ids.add(result.event_id)
        request = self.requests[result.operation_id]
        if result.attempt < request.attempt:
            self.stale_result_ids.add(result.event_id)
            return
        previous = self.observed_results.get(result.operation_id)
        if previous is not None:
            if previous.success != result.success:
                self.conflict_result_ids.add(result.event_id)
                self.status[result.operation_id] = "CONFLICT"
            return
        self.observed_results[result.operation_id] = result
        self.status[result.operation_id] = "SUCCEEDED" if result.success else "FAILED"

    def retry(self, operation_id: str, event_id: str) -> OperationRequest:
        current = self.requests[operation_id]
        retried = OperationRequest(
            operation_id=operation_id,
            operation_kind=current.operation_kind,
            attempt=current.attempt + 1,
            event_id=event_id,
            correlation_id=current.correlation_id,
        )
        self.requests[operation_id] = retried
        self.status[operation_id] = "REQUESTED"
        self.observed_results.pop(operation_id, None)
        return retried

    def mark_unknown(self, operation_id: str) -> None:
        self.status[operation_id] = "UNKNOWN"


class InventoryEffectAuthority:
    """Inventory-owned evidence for whether a reservation side effect occurred."""

    def __init__(self) -> None:
        self.effects: set[str] = set()
        self.application_count = 0

    def apply_reservation(self, request: OperationRequest) -> bool:
        if request.operation_id in self.effects:
            return False
        self.effects.add(request.operation_id)
        self.application_count += 1
        return True


class RecordingTransport:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)


def result(
    operation_id: str,
    attempt: int,
    event_id: str,
    success: bool,
    causation_id: str = "action-1",
) -> OperationResult:
    return OperationResult(operation_id, attempt, event_id, success, causation_id)


def requested(operation_id: str = "O1", attempt: int = 1) -> OperationRequest:
    return OperationRequest(operation_id, "inventory.reserve", attempt, f"request-{attempt}", "C1")


def test_result_loss_after_successful_side_effect() -> None:
    ledger = ExecutionLedger()
    inventory = InventoryEffectAuthority()
    ledger.request(requested())
    assert inventory.apply_reservation(ledger.requests["O1"]) is True

    ledger.mark_unknown("O1")

    assert ledger.status["O1"] == "UNKNOWN"
    assert inventory.effects == {"O1"}
    assert ledger.status["O1"] != "FAILED"


def test_process_crash_before_recording_result() -> None:
    ledger = ExecutionLedger()
    inventory = InventoryEffectAuthority()
    ledger.request(requested())
    inventory.apply_reservation(ledger.requests["O1"])
    restarted = ExecutionLedger(
        requests=dict(ledger.requests),
        status=dict(ledger.status),
        observed_results=dict(ledger.observed_results),
        applied_result_ids=set(ledger.applied_result_ids),
    )

    restarted.mark_unknown("O1")

    assert restarted.status["O1"] == "UNKNOWN"
    assert restarted.observed_results == {}
    assert inventory.effects == {"O1"}


def test_domain_crash_before_result_publication() -> None:
    inventory = InventoryEffectAuthority()
    request = requested()
    inventory.apply_reservation(request)
    domain_restarted = InventoryEffectAuthority()

    assert domain_restarted.effects == set()
    assert inventory.effects == {"O1"}
    assert "O1" not in domain_restarted.effects


def test_duplicate_operation_identity() -> None:
    inventory = InventoryEffectAuthority()
    request = requested()

    assert inventory.apply_reservation(request) is True
    assert inventory.apply_reservation(request) is False
    assert inventory.application_count == 1


def test_distinct_operation_identities_are_distinct_operations() -> None:
    inventory = InventoryEffectAuthority()

    assert inventory.apply_reservation(requested("O1")) is True
    assert inventory.apply_reservation(requested("O2")) is True
    assert inventory.application_count == 2
    assert inventory.effects == {"O1", "O2"}


def test_duplicate_result_does_not_repeat_business_effect() -> None:
    ledger = ExecutionLedger()
    ledger.request(requested())
    completed = result("O1", 1, "result-1", True)

    ledger.mark_result(completed)
    ledger.mark_result(completed)

    assert ledger.status["O1"] == "SUCCEEDED"
    assert ledger.applied_result_ids == {"result-1"}


def test_stale_result_does_not_regress_process() -> None:
    ledger = ExecutionLedger()
    ledger.request(requested())
    ledger.retry("O1", "request-2")
    ledger.mark_result(result("O1", 1, "late-attempt-1", True))

    assert ledger.status["O1"] == "REQUESTED"
    assert ledger.stale_result_ids == {"late-attempt-1"}


def test_same_operation_conflicting_results_are_not_silently_resolved() -> None:
    ledger = ExecutionLedger()
    ledger.request(requested())
    ledger.mark_result(result("O1", 1, "result-success", True))
    ledger.mark_result(result("O1", 1, "result-failure", False))

    assert ledger.status["O1"] == "CONFLICT"
    assert ledger.conflict_result_ids == {"result-failure"}
    assert ledger.observed_results["O1"].event_id == "result-success"


def test_process_durability_differs_from_operation_durability() -> None:
    ledger = ExecutionLedger()
    inventory = InventoryEffectAuthority()
    ledger.request(requested())
    inventory.apply_reservation(ledger.requests["O1"])
    ledger_copy = ExecutionLedger(requests=dict(ledger.requests), status=dict(ledger.status))

    assert ledger_copy.requests["O1"].operation_id == "O1"
    assert ledger_copy.status["O1"] == "REQUESTED"
    assert ledger_copy.status["O1"] != "SUCCEEDED"
    assert inventory.effects == {"O1"}


def test_event_durability_differs_from_side_effect_durability() -> None:
    transport = RecordingTransport()
    inventory = InventoryEffectAuthority()
    request_event = Event(
        "capability.invocation",
        "ORD-1",
        "order",
        {"operation_id": "O1"},
        metadata={"correlation_id": "C1", "causation_id": "order-1"},
    )
    transport.publish(request_event)
    inventory.apply_reservation(requested())

    assert transport.events[0].event_id == request_event.event_id
    assert inventory.effects == {"O1"}
    assert not any(event.event_type.endswith("result") for event in transport.events)


def test_capability_invoker_publishes_request_but_does_not_execute_operation() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.register(Capability("inventory.reserve", "Reserve", "Reserve stock", metadata={"owner": "inventory"}))
    transport = RecordingTransport()
    invoker = CapabilityInvoker(registry, transport)

    invoker.invoke(
        Intent("inventory.reserve", {"operation_id": "O1"}),
        entity_id="ORD-1",
        entity_type="order",
        context={"correlation_id": "C1", "causation_id": "order-action"},
    )

    assert len(transport.events) == 1
    assert transport.events[0].event_type == "capability.invocation"
    assert transport.events[0].metadata["correlation_id"] == "C1"
    assert "result" not in transport.events[0].event_type


def test_process_state_does_not_become_domain_authority() -> None:
    ledger = ExecutionLedger()
    ledger.request(requested())
    ledger.mark_result(result("O1", 1, "result-1", True))

    assert ledger.status["O1"] == "SUCCEEDED"
    assert not hasattr(ledger, "available_stock")
    assert not hasattr(ledger, "reservation_record")


def test_execution_identities_are_distinct() -> None:
    request = requested()
    completed = result("O1", 1, "result-1", True, "action-1")

    assert request.event_id != completed.event_id
    assert request.correlation_id != request.operation_id
    assert completed.causation_id != completed.event_id


def test_no_generic_execution_runtime_was_introduced() -> None:
    source = inspect.getsource(ExecutionLedger)
    assert "WorkflowEngine" not in source
    assert "ExactlyOnceManager" not in source
    assert "ProcessStore" not in source
    assert "Agent" not in source
