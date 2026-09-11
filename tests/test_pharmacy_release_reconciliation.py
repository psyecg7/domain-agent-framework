"""Release-recovery reconciliation contract: Inventory proves its own effects."""

from __future__ import annotations

from dataclasses import dataclass

from agent_app import CapabilityInvoker
from agent_core import Capability, Event, InMemoryCapabilityRegistry, Intent
from agent_delta import DeltaReservationEvidenceStore, InventoryReservationAuthority


class DurableInventoryReleaseAuthority:
    """Inventory owns release effects and evidence keyed by release operation ID."""

    def __init__(self, evidence: DeltaReservationEvidenceStore, reservations: set[str]) -> None:
        self.authority = InventoryReservationAuthority(evidence)
        self.reservations = reservations
        self.release_effects = 0

    def release(self, reservation_operation_id: str, release_operation_id: str) -> bool:
        prior = self.authority.reconcile(release_operation_id)
        if prior == "EXISTS":
            return True
        if prior != "ABSENT" or reservation_operation_id not in self.reservations:
            return False
        self.reservations.remove(reservation_operation_id)
        self.authority.reserve(
            release_operation_id,
            evidence={
                "operation_kind": "inventory.reservation.release",
                "reservation_operation_id": reservation_operation_id,
            },
        )
        self.release_effects += 1
        return True

    def reconcile_release(self, release_operation_id: str) -> str:
        return self.authority.reconcile(release_operation_id)


class InventoryReleaseReconciliationApplication:
    """Inventory capability handler for authoritative release-effect evidence."""

    def __init__(self, inventory: DurableInventoryReleaseAuthority) -> None:
        self.inventory = inventory

    def handle(self, event: Event) -> Event:
        intent = event.payload.get("intent", {})
        parameters = intent.get("parameters", {}) if isinstance(intent, dict) else {}
        release_operation_id = parameters.get("release_operation_id")
        if event.metadata.get("capability_id") != "inventory.reservation.release.reconcile":
            raise ValueError("Unexpected reconciliation capability")
        if not isinstance(release_operation_id, str) or not release_operation_id:
            raise ValueError("Release reconciliation requires release_operation_id")
        return Event(
            "inventory.reservation.release.reconciled",
            event.entity_id,
            event.entity_type,
            {
                "release_operation_id": release_operation_id,
                "release_status": self.inventory.reconcile_release(release_operation_id),
            },
            source="inventory",
            metadata={
                "correlation_id": event.metadata["correlation_id"],
                "causation_id": event.event_id,
            },
        )


@dataclass
class OrderReleaseRecovery:
    """Order observes Inventory's answer; it does not invent a release outcome."""

    release_operation_id: str
    correlation_id: str
    status: str = "UNKNOWN"

    def snapshot(self) -> dict[str, str]:
        return {
            "release_operation_id": self.release_operation_id,
            "correlation_id": self.correlation_id,
            "status": self.status,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, str]) -> "OrderReleaseRecovery":
        return cls(**snapshot)

    def observe(self, event: Event) -> None:
        if event.event_type != "inventory.reservation.release.reconciled":
            return
        if event.metadata.get("correlation_id") != self.correlation_id:
            return
        if event.payload.get("release_operation_id") != self.release_operation_id:
            return
        outcome = event.payload.get("release_status")
        transitions = {
            "EXISTS": "CONFIRMED",
            "ABSENT": "RETRY_DECISION_REQUIRED",
            "CONFLICT": "CONFLICT",
            "STILL_UNKNOWN": "UNKNOWN",
        }
        if outcome not in transitions:
            raise ValueError("Inventory returned an unsupported release reconciliation outcome")
        self.status = transitions[outcome]


class Transport:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)


def registry() -> InMemoryCapabilityRegistry:
    capabilities = InMemoryCapabilityRegistry()
    capabilities.register(Capability(
        "inventory.reservation.release.reconcile",
        "Reconcile a reservation release",
        "Inventory returns authoritative evidence for one release operation.",
        metadata={"owner": "inventory", "intent_types": ("inventory.reservation.release.reconcile",)},
    ))
    return capabilities


def request_reconciliation(recovery: OrderReleaseRecovery, transport: Transport) -> Event:
    CapabilityInvoker(registry(), transport).invoke(
        Intent("inventory.reservation.release.reconcile", {"release_operation_id": recovery.release_operation_id}),
        entity_id="ORD-1",
        entity_type="order",
        context={
            "correlation_id": recovery.correlation_id,
            "causation_id": f"reconcile:{recovery.release_operation_id}",
        },
    )
    return transport.events[0]


def test_lost_release_result_is_resolved_by_inventory_evidence_after_order_restart(tmp_path) -> None:
    release_operation_id = "process-1:inventory.release"
    reservation_operation_id = "process-1:inventory.reserve"
    inventory = DurableInventoryReleaseAuthority(
        DeltaReservationEvidenceStore(tmp_path / "release-evidence"),
        {reservation_operation_id},
    )

    # Inventory performed the release, but its normal result event was lost.
    assert inventory.release(reservation_operation_id, release_operation_id) is True
    recovery = OrderReleaseRecovery(release_operation_id, "buy-1")
    restarted = OrderReleaseRecovery.restore(recovery.snapshot())
    transport = Transport()

    result = InventoryReleaseReconciliationApplication(inventory).handle(
        request_reconciliation(restarted, transport)
    )
    restarted.observe(result)

    assert restarted.status == "CONFIRMED"
    assert inventory.reservations == set()
    assert inventory.release_effects == 1
    assert result.payload["release_status"] == "EXISTS"
    assert result.metadata["correlation_id"] == "buy-1"


def test_release_reconciliation_preserves_inventory_authority_for_non_exists_outcomes(tmp_path) -> None:
    evidence = DeltaReservationEvidenceStore(tmp_path / "release-evidence")
    inventory = DurableInventoryReleaseAuthority(evidence, set())
    application = InventoryReleaseReconciliationApplication(inventory)

    absent = OrderReleaseRecovery("release-absent", "buy-absent")
    absent.observe(application.handle(request_reconciliation(absent, Transport())))
    assert absent.status == "RETRY_DECISION_REQUIRED"

    evidence.record("release-conflict", "EXISTS")
    evidence.record("release-conflict", "STILL_UNKNOWN")
    conflict = OrderReleaseRecovery("release-conflict", "buy-conflict")
    conflict.observe(application.handle(request_reconciliation(conflict, Transport())))
    assert conflict.status == "CONFLICT"

    class UnavailableInventory(DurableInventoryReleaseAuthority):
        def reconcile_release(self, release_operation_id: str) -> str:
            return "STILL_UNKNOWN"

    unknown = OrderReleaseRecovery("release-unknown", "buy-unknown")
    unknown_application = InventoryReleaseReconciliationApplication(UnavailableInventory(evidence, set()))
    unknown.observe(unknown_application.handle(request_reconciliation(unknown, Transport())))
    assert unknown.status == "UNKNOWN"
