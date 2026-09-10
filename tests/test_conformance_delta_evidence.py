from agent_conformance import (
    assert_duplicate_operation_is_idempotent,
    assert_reconciliation_outcome,
    assert_restart_reconciles_effect,
)
from agent_delta import DeltaReservationEvidenceStore, InventoryReservationAuthority


def test_delta_reservation_evidence_meets_generic_operation_conformance(tmp_path) -> None:
    path = tmp_path / "reservation-evidence"
    authority = InventoryReservationAuthority(DeltaReservationEvidenceStore(path))
    effects: list[str] = []

    def apply(operation_id: str) -> None:
        if authority.reserve(operation_id, evidence={"operation_kind": "inventory.reserve"}):
            effects.append(operation_id)

    assert_duplicate_operation_is_idempotent(
        "reserve-duplicate",
        apply=apply,
        effect_count=lambda: len(effects),
    )
    assert_restart_reconciles_effect(
        "reserve-restart",
        apply=apply,
        reconcile_after_restart=InventoryReservationAuthority(
            DeltaReservationEvidenceStore(path)
        ).reconcile,
    )
    assert_reconciliation_outcome(
        "reserve-missing",
        reconcile=authority.reconcile,
        expected="ABSENT",
    )
