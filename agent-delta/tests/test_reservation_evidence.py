from agent_delta import DeltaReservationEvidenceStore, InventoryReservationAuthority


def test_reservation_evidence_persists_across_restart(tmp_path) -> None:
    path = tmp_path / "reservation-evidence"
    authority = InventoryReservationAuthority(DeltaReservationEvidenceStore(path))

    assert authority.reserve("RES-123", evidence={"quantity": 2}) is True
    assert authority.reserve("RES-123", evidence={"quantity": 2}) is False

    restarted = InventoryReservationAuthority(DeltaReservationEvidenceStore(path))

    assert restarted.reconcile("RES-123") == "EXISTS"
    assert restarted.evidence_store.evidence_for("RES-123") == {"quantity": 2}


def test_conflicting_effect_evidence_is_not_silently_overwritten(tmp_path) -> None:
    store = DeltaReservationEvidenceStore(tmp_path / "reservation-evidence")
    store.record("RES-123", "EXISTS")

    assert store.record("RES-123", "STILL_UNKNOWN") is True
    assert store.reconcile("RES-123") == "CONFLICT"
