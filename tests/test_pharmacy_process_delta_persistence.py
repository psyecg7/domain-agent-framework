"""Delta substitution tests for the frozen purchase-process snapshot contract."""

import pytest

from agent_delta import DeltaProcessStore, ProcessStoreUnavailable
from test_pharmacy_process_semantics import (
    PurchaseProcess,
    make_process,
    prepare_execution,
    result_event,
    valid_fact_events,
)
from test_pharmacy_process_vs_workflow import order_result, reservation_result


def test_process_snapshot_can_survive_restart_and_replay_with_delta(tmp_path) -> None:
    store = DeltaProcessStore(tmp_path / "purchase-processes")
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


def test_process_restart_after_reservation_preserves_identity_and_effect_with_delta(tmp_path) -> None:
    store = DeltaProcessStore(tmp_path / "purchase-processes")
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


def test_crash_restart_preserves_partial_completion_without_reinference(tmp_path) -> None:
    store = DeltaProcessStore(tmp_path / "purchase-processes")
    process = make_process("partial-crash-1")
    prepare_execution(process)
    process.observe(reservation_result(process, success=True))
    process.observe(order_result(process, success=False))
    store.save(process)
    del process

    snapshot = store.load("process-1")
    recovered = PurchaseProcess("process-1", "ORD-1", "P123", "partial-crash-1", snapshot=snapshot)

    assert recovered.status == "PARTIALLY_COMPLETED"
    assert recovered.operations["process-1:inventory.reserve"]["status"] == "SUCCEEDED"
    assert recovered.operations["process-1:order.create"]["status"] == "FAILED"


def test_store_unavailable_blocks_reconstruction_without_empty_process_fallback(tmp_path) -> None:
    store = DeltaProcessStore(tmp_path / "purchase-processes")

    def unavailable_records():
        raise OSError("Delta table is unavailable")

    store._records = unavailable_records

    with pytest.raises(ProcessStoreUnavailable):
        store.load("process-1")


def test_store_unavailable_on_write_is_reported_narrowly(tmp_path) -> None:
    store = DeltaProcessStore(tmp_path / "purchase-processes")

    def unavailable_write(records):
        raise OSError("Delta table is unavailable")

    store._write = unavailable_write

    with pytest.raises(ProcessStoreUnavailable):
        store.save(make_process())


def test_invalid_process_id_is_not_disguised_as_storage_unavailability(tmp_path) -> None:
    store = DeltaProcessStore(tmp_path / "purchase-processes")

    with pytest.raises(ValueError):
        store.load("")
