from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import inspect

from agent_application import CapabilityInvoker
from agent_delta import DeltaReservationEvidenceStore, InventoryReservationAuthority
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent


class Transport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.handlers: dict[str, list[Any]] = {}

    def subscribe(self, event_type: str, handler: Any) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for handler in self.handlers.get(event.event_type, []):
            handler(event)


@dataclass
class ReservationAuthority:
    statuses: dict[str, str] = field(default_factory=dict)
    reservation_mutations: int = 0

    def reserve(self, operation_id: str) -> None:
        if operation_id not in self.statuses:
            self.statuses[operation_id] = "EXISTS"
            self.reservation_mutations += 1

    def reconcile(self, operation_id: str) -> str:
        return self.statuses.get(operation_id, "ABSENT")


@dataclass
class PublicationAuthority:
    statuses: dict[str, str] = field(default_factory=dict)

    def reconcile(self, operation_id: str) -> str:
        return self.statuses.get(operation_id, "STILL_UNKNOWN")


@dataclass
class OrderProcess:
    process_id: str
    correlation_id: str
    operation_id: str
    attempt_id: str
    status: str = "UNKNOWN"
    reconciliation_results: dict[str, str] = field(default_factory=dict)
    reconciliation_sequences: dict[str, int] = field(default_factory=dict)
    applied_event_ids: set[str] = field(default_factory=set)
    latest_reconciliation_sequence: int = 0
    next_reconciliation_sequence: int = 1

    def sequence_for(self, reconciliation_id: str) -> int:
        """Assign request order once; duplicate requests retain their order."""
        sequence = self.reconciliation_sequences.get(reconciliation_id)
        if sequence is not None:
            return sequence
        sequence = self.next_reconciliation_sequence
        self.reconciliation_sequences[reconciliation_id] = sequence
        self.next_reconciliation_sequence += 1
        return sequence

    def observe(self, event: Event) -> None:
        if event.metadata.get("correlation_id") != self.correlation_id:
            return
        if event.event_id in self.applied_event_ids:
            return
        self.applied_event_ids.add(event.event_id)
        if event.event_type != "inventory.reservation.reconciled":
            return
        if event.payload.get("operation_id") != self.operation_id:
            return
        if event.payload.get("attempt_id") != self.attempt_id:
            return
        reconciliation_id = event.payload.get("reconciliation_id")
        if not isinstance(reconciliation_id, str):
            return
        if reconciliation_id in self.reconciliation_results:
            return
        sequence = event.payload.get("reconciliation_sequence")
        if not isinstance(sequence, int) or sequence < 1 or sequence <= self.latest_reconciliation_sequence:
            return
        self.latest_reconciliation_sequence = sequence
        status = event.payload["reservation_status"]
        self.reconciliation_results[reconciliation_id] = status
        if status == "EXISTS":
            self.status = "SUCCEEDED"
        elif status == "ABSENT":
            self.status = "FAILED"
        elif status == "CONFLICT":
            self.status = "CONFLICT"
        else:
            self.status = "STILL_UNKNOWN"


class InventoryReconciliationApplication:
    def __init__(self, authority: ReservationAuthority) -> None:
        self.authority = authority
        self.registry = InMemoryCapabilityRegistry()
        self.registry.register(
            Capability(
                "inventory.reservation.reconcile",
                "Reconcile reservation",
                "Return authoritative reservation status",
                metadata={"owner": "inventory", "intent_types": ("inventory.reservation.reconcile",)},
            )
        )
        self.transport = Transport()
        self.invoker = CapabilityInvoker(self.registry, self.transport)
        self.transport.subscribe("capability.invocation", self._handle)

    def _handle(self, event: Event) -> None:
        intent = event.payload["intent"]
        parameters = intent["parameters"]
        status = self.authority.reconcile(parameters["operation_id"])
        self.transport.publish(
            Event(
                "inventory.reservation.reconciled",
                event.entity_id,
                "order",
                {
                    "operation_id": parameters["operation_id"],
                    "attempt_id": parameters["attempt_id"],
                    "reconciliation_id": parameters["reconciliation_id"],
                    "reconciliation_sequence": parameters["reconciliation_sequence"],
                    "reservation_status": status,
                },
                source="inventory",
                metadata={
                    "correlation_id": event.metadata["correlation_id"],
                    "causation_id": event.event_id,
                },
            )
        )

    def reconcile(self, process: OrderProcess, reconciliation_id: str) -> None:
        sequence = process.sequence_for(reconciliation_id)
        self.invoker.invoke(
            Intent(
                "inventory.reservation.reconcile",
                {
                    "operation_id": process.operation_id,
                    "attempt_id": process.attempt_id,
                    "reconciliation_id": reconciliation_id,
                    "reconciliation_sequence": sequence,
                },
            ),
            entity_id=process.process_id,
            entity_type="order_process",
            context={
                "correlation_id": process.correlation_id,
                "causation_id": f"reconcile:{reconciliation_id}",
            },
        )


class PIMPublicationReconciliation:
    def __init__(self, authority: PublicationAuthority) -> None:
        self.authority = authority

    def reconcile(self, operation_id: str) -> str:
        return self.authority.reconcile(operation_id)


def test_unknown_resolves_to_success_through_inventory_capability() -> None:
    authority = ReservationAuthority()
    authority.reserve("RES-123")
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(authority)

    application.reconcile(process, "REC-001")
    process.observe(application.transport.events[-1])

    assert process.status == "SUCCEEDED"
    assert process.reconciliation_results == {"REC-001": "EXISTS"}
    assert authority.reservation_mutations == 1
    assert application.transport.events[0].event_type == "capability.invocation"
    assert application.transport.events[1].event_type == "inventory.reservation.reconciled"


def test_unknown_resolves_to_failure_from_authoritative_absence() -> None:
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(ReservationAuthority())

    application.reconcile(process, "REC-002")
    process.observe(application.transport.events[-1])

    assert process.status == "FAILED"
    assert process.reconciliation_results["REC-002"] == "ABSENT"


def test_reconciliation_can_remain_still_unknown() -> None:
    authority = ReservationAuthority()
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(authority)
    authority.statuses["RES-123"] = "STILL_UNKNOWN"

    application.reconcile(process, "REC-003")
    process.observe(application.transport.events[-1])

    assert process.status == "STILL_UNKNOWN"
    assert process.status not in {"SUCCEEDED", "FAILED"}


def test_conflicting_evidence_is_preserved_for_inventory_to_resolve() -> None:
    authority = ReservationAuthority()
    authority.statuses["RES-123"] = "CONFLICT"
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(authority)

    application.reconcile(process, "REC-004")
    process.observe(application.transport.events[-1])

    assert process.status == "CONFLICT"
    assert authority.statuses["RES-123"] == "CONFLICT"


def test_restart_preserves_operation_and_reconciliation_uses_original_identity() -> None:
    authority = ReservationAuthority()
    authority.reserve("RES-123")
    original = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    restarted = OrderProcess(
        original.process_id,
        original.correlation_id,
        original.operation_id,
        original.attempt_id,
        status=original.status,
        reconciliation_results=dict(original.reconciliation_results),
        reconciliation_sequences=dict(original.reconciliation_sequences),
        applied_event_ids=set(original.applied_event_ids),
        latest_reconciliation_sequence=original.latest_reconciliation_sequence,
        next_reconciliation_sequence=original.next_reconciliation_sequence,
    )
    application = InventoryReconciliationApplication(authority)

    application.reconcile(restarted, "REC-005")
    restarted.observe(application.transport.events[-1])

    assert restarted.process_id == original.process_id
    assert restarted.operation_id == "RES-123"
    assert restarted.status == "SUCCEEDED"
    assert authority.reservation_mutations == 1


def test_lost_result_reconciles_from_durable_inventory_effect_evidence(tmp_path) -> None:
    evidence_path = tmp_path / "inventory-reservation-evidence"
    initial_authority = InventoryReservationAuthority(DeltaReservationEvidenceStore(evidence_path))
    assert initial_authority.reserve("RES-123", evidence={"quantity": 2}) is True

    original = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    initial_application = InventoryReconciliationApplication(initial_authority)
    initial_application.reconcile(original, "REC-LOST")
    # The result is deliberately not observed by Order.
    assert original.status == "UNKNOWN"

    restarted_authority = InventoryReservationAuthority(DeltaReservationEvidenceStore(evidence_path))
    restarted = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    restarted_application = InventoryReconciliationApplication(restarted_authority)
    restarted_application.reconcile(restarted, "REC-AFTER-RESTART")
    restarted.observe(restarted_application.transport.events[-1])

    assert restarted.status == "SUCCEEDED"
    assert restarted_authority.reconcile("RES-123") == "EXISTS"


def test_duplicate_reconciliation_is_safe_and_does_not_mutate_reservation() -> None:
    authority = ReservationAuthority()
    authority.reserve("RES-123")
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(authority)

    application.reconcile(process, "REC-006")
    first_result = application.transport.events[-1]
    application.reconcile(process, "REC-006")
    second_result = application.transport.events[-1]
    process.observe(first_result)
    process.observe(second_result)

    assert authority.reservation_mutations == 1
    assert process.reconciliation_results == {"REC-006": "EXISTS"}
    assert first_result.event_id != second_result.event_id


def test_duplicate_reconciliation_identity_is_filtered_before_sequence_handling() -> None:
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    first = Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order",
        {
            "operation_id": "RES-123",
            "attempt_id": "ATTEMPT-1",
            "reconciliation_id": "REC-1",
            "reconciliation_sequence": 1,
            "reservation_status": "EXISTS",
        },
        metadata={"correlation_id": "C1", "causation_id": "request-1"},
    )
    duplicate_with_later_sequence = Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order",
        {
            "operation_id": "RES-123",
            "attempt_id": "ATTEMPT-1",
            "reconciliation_id": "REC-1",
            "reconciliation_sequence": 2,
            "reservation_status": "ABSENT",
        },
        metadata={"correlation_id": "C1", "causation_id": "request-1-replayed"},
    )

    process.observe(first)
    process.observe(duplicate_with_later_sequence)

    assert process.status == "SUCCEEDED"
    assert process.reconciliation_results == {"REC-1": "EXISTS"}
    assert process.latest_reconciliation_sequence == 1


def test_late_reconciliation_result_does_not_overwrite_newer_reconciliation() -> None:
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    old = Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order",
        {"operation_id": "RES-123", "attempt_id": "ATTEMPT-1", "reconciliation_id": "REC-OLD", "reconciliation_sequence": 1, "reservation_status": "ABSENT"},
        metadata={"correlation_id": "C1", "causation_id": "old-request"},
    )
    new = Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order",
        {"operation_id": "RES-123", "attempt_id": "ATTEMPT-1", "reconciliation_id": "REC-NEW", "reconciliation_sequence": 2, "reservation_status": "EXISTS"},
        metadata={"correlation_id": "C1", "causation_id": "new-request"},
    )

    process.observe(new)
    process.observe(old)

    assert process.status == "SUCCEEDED"
    assert process.reconciliation_results == {"REC-NEW": "EXISTS"}


def test_application_emits_process_assigned_sequence_and_enforces_it_on_results() -> None:
    authority = ReservationAuthority({"RES-123": "EXISTS"})
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(authority)

    application.reconcile(process, "REC-OLD")
    old_result = application.transport.events[-1]
    application.reconcile(process, "REC-NEW")
    new_result = application.transport.events[-1]

    assert old_result.payload["reconciliation_sequence"] == 1
    assert new_result.payload["reconciliation_sequence"] == 2
    process.observe(new_result)
    process.observe(old_result)

    assert process.status == "SUCCEEDED"
    assert process.reconciliation_results == {"REC-NEW": "EXISTS"}


def test_reconciliation_after_explicit_retry_preserves_attempt_identity() -> None:
    authority = ReservationAuthority()
    authority.reserve("RES-123")
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-2")
    application = InventoryReconciliationApplication(authority)

    application.reconcile(process, "REC-007")
    result_event = application.transport.events[-1]
    process.observe(result_event)

    assert result_event.payload["operation_id"] == "RES-123"
    assert result_event.payload["attempt_id"] == "ATTEMPT-2"
    assert process.status == "SUCCEEDED"


def test_stale_attempt_result_cannot_resolve_the_current_attempt() -> None:
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-2")
    stale_result = Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order",
        {
            "operation_id": "RES-123",
            "attempt_id": "ATTEMPT-1",
            "reconciliation_id": "REC-OLD",
            "reconciliation_sequence": 1,
            "reservation_status": "EXISTS",
        },
        metadata={"correlation_id": "C1", "causation_id": "old-request"},
    )

    process.observe(stale_result)

    assert process.status == "UNKNOWN"
    assert process.reconciliation_results == {}


def test_human_and_agent_reconciliation_share_capability_event_boundary() -> None:
    authority = ReservationAuthority()
    authority.reserve("RES-123")
    application = InventoryReconciliationApplication(authority)
    human_process = OrderProcess("process-human", "C-HUMAN", "RES-123", "ATTEMPT-1")

    application.invoker.invoke(
        Intent(
            "inventory.reservation.reconcile",
            {
                "operation_id": "RES-123",
                "attempt_id": "ATTEMPT-1",
                "reconciliation_id": "REC-HUMAN",
                "reconciliation_sequence": human_process.sequence_for("REC-HUMAN"),
            },
        ),
        entity_id=human_process.process_id,
        entity_type="order_process",
        context={"correlation_id": human_process.correlation_id, "causation_id": "human-intent"},
    )

    assert application.transport.events[0].event_type == "capability.invocation"
    assert application.transport.events[0].metadata["causation_id"] == "human-intent"


def test_reconciliation_is_not_compensation_or_business_mutation() -> None:
    authority = ReservationAuthority()
    authority.reserve("RES-123")
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    application = InventoryReconciliationApplication(authority)
    before = authority.reservation_mutations

    application.reconcile(process, "REC-008")

    assert authority.reservation_mutations == before
    assert not any(event.metadata.get("capability_id") == "inventory.reservation.release" for event in application.transport.events)


def test_inventory_unavailable_keeps_unknown(tmp_path) -> None:
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    store = DeltaReservationEvidenceStore(tmp_path / "unavailable-evidence")

    def unavailable_records():
        raise OSError("Delta table is unavailable")

    store._records = unavailable_records
    application = InventoryReconciliationApplication(InventoryReservationAuthority(store))
    application.reconcile(process, "REC-UNAVAILABLE")
    process.observe(application.transport.events[-1])

    assert process.status == "STILL_UNKNOWN"
    assert [event.event_type for event in application.transport.events] == [
        "capability.invocation",
        "inventory.reservation.reconciled",
    ]


def test_pim_reconciliation_has_similar_shape_but_different_business_meaning() -> None:
    authority = PublicationAuthority({"PUB-7": "PUBLISHED"})
    pim = PIMPublicationReconciliation(authority)
    inventory = ReservationAuthority({"RES-123": "EXISTS"})

    assert pim.reconcile("PUB-7") == "PUBLISHED"
    assert inventory.reconcile("RES-123") == "EXISTS"
    assert "PUB-7" not in inventory.statuses


def test_reconciliation_has_no_generic_framework_dependency() -> None:
    source = inspect.getsource(InventoryReconciliationApplication)
    assert "ReconciliationService" not in source
    assert "WorkflowEngine" not in source
    assert "Agent" not in source
    assert ".process(" not in source
