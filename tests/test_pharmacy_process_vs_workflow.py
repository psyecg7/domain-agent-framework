from __future__ import annotations

import inspect
from dataclasses import dataclass, field

import pytest

from agent_app import CapabilityInvoker
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent
from test_pharmacy_process_semantics import (
    ProcessStore,
    PurchaseProcess,
    make_process,
    prepare_execution,
    result_event,
    valid_fact_events,
)


class ReservationDomain:
    """Inventory-owned invariant: one business reservation operation has one effect."""

    def __init__(self) -> None:
        self.reservations: set[str] = set()
        self.released: set[str] = set()
        self.effects = 0
        self.release_effects = 0

    def handle(self, event: Event) -> bool:
        operation_id = event.payload["operation_id"]
        if operation_id in self.reservations:
            return False
        self.reservations.add(operation_id)
        self.effects += 1
        return True

    def release(self, event: Event) -> Event:
        """Inventory-owned recovery effect and its ordinary result event."""
        intent = event.payload.get("intent", {})
        parameters = intent.get("parameters", {}) if isinstance(intent, dict) else {}
        operation_id = parameters.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("Release invocation requires an operation_id")
        released = operation_id in self.released
        if not released and operation_id in self.reservations:
            self.reservations.remove(operation_id)
            self.released.add(operation_id)
            self.release_effects += 1
            released = True
        return Event(
            "inventory.reservation.release.result",
            event.entity_id,
            event.entity_type,
            {"operation_id": operation_id, "success": released},
            source="inventory",
            metadata={
                "correlation_id": event.metadata["correlation_id"],
                "causation_id": event.event_id,
            },
        )


@dataclass
class RecoveryReleaseOutbox:
    """Order application evidence for an explicitly chosen Inventory release.

    This is intentionally a test-domain outbox, not an agent-core primitive or
    production persistence adapter. ``snapshot()`` simulates the durable state
    that a real Order application must write before handing a release request
    to a fallible transport.
    """

    entries: dict[str, dict[str, str]] = field(default_factory=dict)

    def schedule(self, process: PurchaseProcess, reservation_operation_id: str) -> str:
        release_operation_id = f"{process.process_id}:inventory.release"
        self.entries.setdefault(
            release_operation_id,
            {
                "status": "PENDING",
                "reservation_operation_id": reservation_operation_id,
                "correlation_id": process.correlation_id,
            },
        )
        return release_operation_id

    def publish(self, release_operation_id: str, registry: InMemoryCapabilityRegistry, transport: object) -> None:
        entry = self.entries[release_operation_id]
        if entry["status"] != "PENDING":
            return
        CapabilityInvoker(registry, transport).invoke(
            Intent(
                "inventory.reservation.release",
                {"operation_id": entry["reservation_operation_id"]},
                idempotency_key=release_operation_id,
            ),
            entity_id="ORD-1",
            entity_type="order",
            context={
                "correlation_id": entry["correlation_id"],
                "causation_id": release_operation_id,
            },
        )
        entry["status"] = "PUBLISHED"

    def confirm(self, release_operation_id: str, result: Event) -> None:
        entry = self.entries[release_operation_id]
        if (
            result.payload.get("operation_id") != entry["reservation_operation_id"]
            or result.payload.get("success") is not True
            or result.metadata.get("correlation_id") != entry["correlation_id"]
        ):
            raise ValueError("Release result does not confirm this recovery operation")
        entry["status"] = "CONFIRMED"

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {key: dict(value) for key, value in self.entries.items()}


def reservation_result(process: PurchaseProcess, *, success: bool, event_id: str = "reservation-result") -> Event:
    return result_event(
        "inventory.reservation.result",
        process.correlation_id,
        {
            "operation_id": "process-1:inventory.reserve",
            "operation_kind": "inventory.reserve",
            "success": success,
        },
        causation_id=event_id,
    )


def order_result(process: PurchaseProcess, *, success: bool) -> Event:
    return result_event(
        "order.created.result",
        process.correlation_id,
        {
            "operation_id": "process-1:order.create",
            "operation_kind": "order.create",
            "success": success,
        },
    )


def test_reservation_succeeds_order_creation_fails() -> None:
    process = make_process()
    prepare_execution(process)
    process.observe(reservation_result(process, success=True))
    process.observe(order_result(process, success=False))

    assert process.status == "PARTIALLY_COMPLETED"
    assert process.operations["process-1:inventory.reserve"]["status"] == "SUCCEEDED"
    assert process.operations["process-1:order.create"]["status"] == "FAILED"
    assert process.recovery_reason is None


def test_process_restart_after_reservation_preserves_identity_and_effect() -> None:
    store = ProcessStore()
    process = make_process("restart-1")
    prepare_execution(process)
    reservation = reservation_result(process, success=True)
    process.observe(reservation)
    store.save(process)

    restarted = PurchaseProcess(
        process.process_id,
        process.order_id,
        process.product_id,
        process.correlation_id,
        snapshot=store.load(process.process_id),
    )
    restarted.observe(reservation)
    restarted.observe(order_result(restarted, success=False))

    assert restarted.process_id == process.process_id
    assert restarted.correlation_id == "restart-1"
    assert restarted.operations["process-1:inventory.reserve"]["status"] == "SUCCEEDED"
    assert restarted.status == "PARTIALLY_COMPLETED"
    assert len(restarted.applied_event_ids) == len(process.applied_event_ids) + 1


def test_duplicate_reservation_request_is_an_inventory_invariant() -> None:
    process = make_process()
    operation_id = "process-1:inventory.reserve"
    reservation_domain = ReservationDomain()
    first = Event(
        "capability.invocation",
        "ORD-1",
        "order",
        {"operation_id": operation_id},
        metadata={"correlation_id": process.correlation_id, "causation_id": "order-request"},
    )
    duplicate = Event(
        "capability.invocation",
        "ORD-1",
        "order",
        {"operation_id": operation_id},
        metadata={"correlation_id": process.correlation_id, "causation_id": "order-request"},
    )

    assert reservation_domain.handle(first) is True
    assert reservation_domain.handle(duplicate) is False
    assert reservation_domain.effects == 1
    assert process.record_operation_request(operation_id, "inventory.reserve") is True
    assert process.record_operation_request(operation_id, "inventory.reserve") is False


def test_recovery_requires_explicit_order_decision_and_remains_event_driven() -> None:
    process = make_process()
    prepare_execution(process)
    inventory = ReservationDomain()
    reservation_request = Event(
        "capability.invocation",
        "ORD-1",
        "order",
        {"operation_id": "process-1:inventory.reserve"},
        source="order",
        metadata={"correlation_id": process.correlation_id, "causation_id": "order-decision"},
    )
    assert inventory.handle(reservation_request) is True
    process.observe(reservation_result(process, success=True))
    process.observe(order_result(process, success=False))
    process.request_recovery("Order failed after Inventory reservation")

    registry = InMemoryCapabilityRegistry()
    registry.register(
        Capability(
            "inventory.reservation.release",
            "Release reservation",
            "Release a reservation after an explicit Order recovery decision.",
            metadata={"owner": "inventory", "intent_types": ("inventory.reservation.release",)},
        )
    )
    published: list[Event] = []

    class Transport:
        def publish(self, event: Event) -> None:
            published.append(event)

    CapabilityInvoker(registry, Transport()).invoke(
        Intent(
            "inventory.reservation.release",
            {"operation_id": "process-1:inventory.reserve"},
        ),
        entity_id="ORD-1",
        entity_type="order",
        context={"correlation_id": process.correlation_id, "causation_id": "order-recovery-decision"},
    )

    release_request = published[0]
    release_result = inventory.release(release_request)

    assert process.status == "RECOVERY_REQUIRED"
    assert release_request.event_type == "capability.invocation"
    assert release_request.metadata["correlation_id"] == process.correlation_id
    assert release_request.metadata["causation_id"] == "order-recovery-decision"
    assert release_result.event_type == "inventory.reservation.release.result"
    assert release_result.payload == {"operation_id": "process-1:inventory.reserve", "success": True}
    assert release_result.metadata["correlation_id"] == process.correlation_id
    assert release_result.metadata["causation_id"] == release_request.event_id
    assert inventory.reservations == set()
    assert inventory.release_effects == 1


def test_failed_release_publish_survives_restart_until_inventory_confirms_recovery() -> None:
    process = make_process("recovery-restart-1")
    prepare_execution(process)
    inventory = ReservationDomain()
    reservation_operation_id = "process-1:inventory.reserve"
    assert inventory.handle(Event(
        "capability.invocation", "ORD-1", "order", {"operation_id": reservation_operation_id}, source="order",
        metadata={"correlation_id": process.correlation_id, "causation_id": "order-decision"},
    )) is True
    process.observe(reservation_result(process, success=True))
    process.observe(order_result(process, success=False))
    process.request_recovery("Order failed after Inventory reservation")

    store = ProcessStore()
    store.save(process)
    outbox = RecoveryReleaseOutbox()
    release_operation_id = outbox.schedule(process, reservation_operation_id)

    registry = InMemoryCapabilityRegistry()
    registry.register(Capability(
        "inventory.reservation.release", "Release reservation", "Explicit recovery only.",
        metadata={"owner": "inventory", "intent_types": ("inventory.reservation.release",)},
    ))

    class FailingTransport:
        def publish(self, event: Event) -> None:
            raise OSError("broker unavailable before accepting release request")

    with pytest.raises(OSError, match="broker unavailable"):
        outbox.publish(release_operation_id, registry, FailingTransport())
    assert outbox.entries[release_operation_id]["status"] == "PENDING"
    assert inventory.reservations == {reservation_operation_id}

    restarted_process = PurchaseProcess(
        process.process_id, process.order_id, process.product_id, process.correlation_id,
        snapshot=store.load(process.process_id),
    )
    restarted_outbox = RecoveryReleaseOutbox(outbox.snapshot())
    published: list[Event] = []

    class Transport:
        def publish(self, event: Event) -> None:
            published.append(event)

    restarted_outbox.publish(release_operation_id, registry, Transport())
    release_request = published[0]
    release_result = inventory.release(release_request)
    restarted_outbox.confirm(release_operation_id, release_result)

    # A broker duplicate remains a single Inventory business release.
    assert inventory.release(release_request).payload["success"] is True
    assert restarted_process.status == "RECOVERY_REQUIRED"
    assert restarted_outbox.entries[release_operation_id]["status"] == "CONFIRMED"
    assert inventory.reservations == set()
    assert inventory.release_effects == 1


def test_temporary_failure_requires_explicit_retry() -> None:
    process = make_process()
    prepare_execution(process)
    operation_id = "process-1:inventory.reserve"
    process.record_temporary_failure(operation_id, "temporary-failure")

    assert process.status == "EXECUTING"
    assert process.operations[operation_id]["status"] == "TEMPORARY_FAILURE"
    process.request_retry(operation_id)
    assert process.operations[operation_id]["status"] == "PENDING"
    assert process.operations[operation_id]["attempts"] == 1


def test_missing_result_does_not_create_false_success() -> None:
    process = make_process()
    prepare_execution(process)

    assert process.status == "EXECUTING"
    assert process.operations["process-1:inventory.reserve"]["status"] == "PENDING"
    assert process.status != "COMPLETED"
    process.mark_timeout("process-1:inventory.reserve")
    assert process.status == "RECOVERY_REQUIRED"


def test_process_identity_survives_restart_and_operation_identity_is_stable() -> None:
    process = make_process("identity-1")
    prepare_execution(process)
    operation_ids = set(process.operations)
    restored = PurchaseProcess(
        process.process_id,
        process.order_id,
        process.product_id,
        process.correlation_id,
        snapshot=process.snapshot(),
    )

    assert restored.process_id == "process-1"
    assert restored.correlation_id == "identity-1"
    assert set(restored.operations) == operation_ids
    assert restored.operations["process-1:inventory.reserve"]["kind"] == "inventory.reserve"


def test_process_does_not_duplicate_domain_state_or_call_agents() -> None:
    process = make_process()
    event = valid_fact_events(process.correlation_id)[1]
    event.payload["available_stock"] = 17
    process.observe(event)
    snapshot = process.snapshot()
    source = inspect.getsource(PurchaseProcess)

    assert snapshot.facts == {"inventory": "VALID"}
    assert "available_stock" not in snapshot.__dict__
    assert "Agent" not in source
    assert "StateStore" not in source
    assert ".process(" not in source


def test_late_out_of_order_result_preserves_correlation_and_causation() -> None:
    process = make_process("late-1")
    for event in valid_fact_events("late-1"):
        process.observe(event)
    process.begin_execution()
    late_reservation = reservation_result(process, success=True, event_id="inventory-action-1")
    process.observe(order_result(process, success=True))
    process.observe(late_reservation)

    assert process.status == "COMPLETED"
    assert process.correlation_id == "late-1"
    assert process.operations["process-1:inventory.reserve"]["last_causation_id"] == "inventory-action-1"
