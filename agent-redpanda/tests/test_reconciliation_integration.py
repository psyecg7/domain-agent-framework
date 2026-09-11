"""Opt-in broker assertions for the frozen 2S reconciliation contract.

Run with ``REDPANDA_BOOTSTRAP_SERVERS=localhost:19092`` after starting the
repository's ``docker-compose.redpanda.yml`` service.
"""

from __future__ import annotations

import importlib.util
import hashlib
import os
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

BOOTSTRAP = os.getenv("REDPANDA_BOOTSTRAP_SERVERS")
if not BOOTSTRAP:
    pytest.skip("set REDPANDA_BOOTSTRAP_SERVERS to run broker integration tests", allow_module_level=True)

pytest.importorskip("confluent_kafka")
from confluent_kafka import Producer

from agent_core import Event
from agent_delta import DeltaReservationEvidenceStore
from agent_redpanda import RedpandaConsumer, RedpandaEventDispatcher, RedpandaEventTransport, RedpandaProducer
from agent_redpanda.runtime import RedpandaAgentRuntime

# The broker suite deliberately reuses the frozen 2S OrderProcess fixture so
# redelivery and ordering exercise the same observation contract as its unit
# tests. Load the file explicitly: a third-party regular ``tests`` package can
# otherwise shadow this repository's namespace package during full collection.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_MODULE_NAME = "_frozen_pharmacy_reconciliation"
_fixture_spec = importlib.util.spec_from_file_location(
    _FIXTURE_MODULE_NAME,
    PROJECT_ROOT / "tests" / "test_pharmacy_reconciliation.py",
)
if _fixture_spec is None or _fixture_spec.loader is None:  # pragma: no cover - repository layout invariant
    raise RuntimeError("unable to load frozen 2S reconciliation fixture")
_fixture_module = importlib.util.module_from_spec(_fixture_spec)
sys.modules[_FIXTURE_MODULE_NAME] = _fixture_module
_fixture_spec.loader.exec_module(_fixture_module)

OrderProcess = _fixture_module.OrderProcess


def _consumer(topic: str, group_id: str, *, max_record_bytes: int = RedpandaConsumer.DEFAULT_MAX_RECORD_BYTES) -> RedpandaConsumer:
    return RedpandaConsumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
        },
        topics=[topic],
        max_record_bytes=max_record_bytes,
    )


def _dispatch_until(dispatcher: RedpandaEventDispatcher, predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        dispatcher.dispatch(timeout=1.0)
        if predicate():
            return
    pytest.fail("broker did not deliver the expected reconciliation event")


def _dispatch_group_until(
    dispatchers: list[RedpandaEventDispatcher], predicate: Callable[[], bool]
) -> None:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        for dispatcher in dispatchers:
            dispatcher.dispatch(timeout=0.5)
        if predicate():
            return
    pytest.fail("consumer group did not deliver the expected keyed operation")


def _result_event(
    *,
    reconciliation_id: str,
    sequence: int,
    status: str,
    event_id: str | None = None,
    correlation_id: str = "C1",
    causation_id: str = "reconcile-request",
) -> Event:
    return Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order",
        payload={
            "operation_id": "RES-123",
            "attempt_id": "ATTEMPT-1",
            "reconciliation_id": reconciliation_id,
            "reconciliation_sequence": sequence,
            "reservation_status": status,
        },
        event_id=event_id or str(uuid.uuid4()),
        source="inventory",
        metadata={"correlation_id": correlation_id, "causation_id": causation_id},
    )


def _transport(topic: str) -> RedpandaEventTransport:
    return RedpandaEventTransport(RedpandaProducer({"bootstrap.servers": BOOTSTRAP}), topic_for_event=lambda _: topic)


def test_handler_failure_redelivers_and_order_process_deduplicates() -> None:
    """An uncommitted delivery repeats; the actual OrderProcess applies it once."""
    prefix = f"agent-2s-redelivery-{uuid.uuid4().hex}"
    topic = f"{prefix}-results"
    event = _result_event(reconciliation_id="REC-1", sequence=1, status="EXISTS")
    _transport(topic).publish(event)
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")

    first_consumer = _consumer(topic, f"{prefix}-order")
    first_dispatcher = RedpandaEventDispatcher(first_consumer)
    first_dispatcher.subscribe(event.event_type, process.observe)

    def fail_after_observation(_: Event) -> None:
        raise RuntimeError("simulated Order crash before offset acknowledgement")

    first_dispatcher.subscribe(event.event_type, fail_after_observation)
    try:
        with pytest.raises(RuntimeError, match="simulated Order crash"):
            _dispatch_until(first_dispatcher, lambda: False)
    finally:
        first_consumer.close()

    restarted_consumer = _consumer(topic, f"{prefix}-order")
    restarted_dispatcher = RedpandaEventDispatcher(restarted_consumer)
    redelivered: list[Event] = []

    def observe_redelivery(delivered: Event) -> None:
        redelivered.append(delivered)
        process.observe(delivered)

    restarted_dispatcher.subscribe(event.event_type, observe_redelivery)
    try:
        _dispatch_until(restarted_dispatcher, lambda: len(redelivered) == 1)
    finally:
        restarted_consumer.close()

    assert redelivered[0].event_id == event.event_id
    assert process.status == "SUCCEEDED"
    assert process.applied_event_ids == {event.event_id}
    assert process.reconciliation_results == {"REC-1": "EXISTS"}


def test_newer_reconciliation_arriving_first_wins_over_late_older_evidence() -> None:
    prefix = f"agent-2s-ordering-{uuid.uuid4().hex}"
    topic = f"{prefix}-results"
    transport = _transport(topic)
    transport.publish(_result_event(reconciliation_id="REC-NEW", sequence=2, status="EXISTS"))
    transport.publish(_result_event(reconciliation_id="REC-OLD", sequence=1, status="ABSENT"))
    process = OrderProcess("process-1", "C1", "RES-123", "ATTEMPT-1")
    consumer = _consumer(topic, f"{prefix}-order")
    dispatcher = RedpandaEventDispatcher(consumer)
    dispatcher.subscribe("inventory.reservation.reconciled", process.observe)
    try:
        _dispatch_until(dispatcher, lambda: len(process.applied_event_ids) == 2)
    finally:
        consumer.close()

    assert process.status == "SUCCEEDED"
    assert process.latest_reconciliation_sequence == 2
    assert process.reconciliation_results == {"REC-NEW": "EXISTS"}


def test_lineage_survives_uncommitted_delivery_and_consumer_restart() -> None:
    prefix = f"agent-2s-lineage-{uuid.uuid4().hex}"
    topic = f"{prefix}-results"
    event = _result_event(
        reconciliation_id="REC-LINEAGE",
        sequence=1,
        status="EXISTS",
        correlation_id="correlation-through-restart",
        causation_id="causation-through-restart",
    )
    _transport(topic).publish(event)

    first_consumer = _consumer(topic, f"{prefix}-order")
    first_dispatcher = RedpandaEventDispatcher(first_consumer)

    def fail(_: Event) -> None:
        raise RuntimeError("simulated crash")

    first_dispatcher.subscribe(event.event_type, fail)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _dispatch_until(first_dispatcher, lambda: False)
    finally:
        first_consumer.close()

    received: list[Event] = []
    restarted_consumer = _consumer(topic, f"{prefix}-order")
    restarted_dispatcher = RedpandaEventDispatcher(restarted_consumer)
    restarted_dispatcher.subscribe(event.event_type, received.append)
    try:
        _dispatch_until(restarted_dispatcher, lambda: len(received) == 1)
    finally:
        restarted_consumer.close()

    assert received[0].event_id == event.event_id
    assert received[0].metadata["correlation_id"] == "correlation-through-restart"
    assert received[0].metadata["causation_id"] == "causation-through-restart"


def test_keyed_delta_evidence_write_has_one_consumer_owner_across_group_handoff(tmp_path) -> None:
    """A same-key operation is serialized by consumer-group ownership, not Delta."""
    prefix = f"agent-2s-ownership-{uuid.uuid4().hex}"
    topic = f"{prefix}-operations"
    group_id = f"{prefix}-inventory"
    operation_id = "RES-SERIALIZED"
    store = DeltaReservationEvidenceStore(tmp_path / "reservation-evidence")
    owners: list[str] = []
    writes: list[bool] = []
    transport = _transport(topic)
    # Publishing first lets the local broker auto-create this isolated topic
    # before either group member subscribes to it.
    transport.publish(Event(
        "inventory.reservation.reconciled",
        operation_id,
        "reservation",
        payload={"operation_id": operation_id},
        source="inventory",
    ))
    consumers = {name: _consumer(topic, group_id) for name in ("worker-a", "worker-b")}
    dispatchers = {name: RedpandaEventDispatcher(consumer) for name, consumer in consumers.items()}

    def record_as(worker: str) -> Callable[[Event], None]:
        def record(event: Event) -> None:
            owners.append(worker)
            writes.append(store.record(event.payload["operation_id"], "EXISTS", evidence={"worker": worker}))

        return record

    for name, dispatcher in dispatchers.items():
        dispatcher.subscribe("inventory.reservation.reconciled", record_as(name))

    closed: set[str] = set()
    try:
        # Both members join one group before either handler is allowed to
        # commit the already-published keyed operation.
        _dispatch_group_until(list(dispatchers.values()), lambda: len(owners) == 1)

        first_owner = owners[0]
        consumers[first_owner].close()
        closed.add(first_owner)
        surviving_dispatchers = [dispatcher for name, dispatcher in dispatchers.items() if name != first_owner]

        transport.publish(Event(
            "inventory.reservation.reconciled",
            operation_id,
            "reservation",
            payload={"operation_id": operation_id},
            source="inventory",
        ))
        _dispatch_group_until(surviving_dispatchers, lambda: len(owners) == 2)
    finally:
        for name, consumer in consumers.items():
            if name not in closed:
                consumer.close()

    assert owners[0] != owners[1]
    assert writes == [True, False]
    assert store.reconcile(operation_id) == "EXISTS"


def test_malformed_broker_bytes_reach_dlq_once_and_source_offset_advances() -> None:
    """A non-JSON payload must not cause an infinite consumer crash loop."""
    prefix = f"agent-dlq-{uuid.uuid4().hex}"
    source_topic, dead_letter_topic = f"{prefix}-source", f"{prefix}-dlq"
    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    producer.produce(source_topic, key="bad-1", value=b"\xffnot-json")
    # Create the DLQ topic before subscribing; the local broker otherwise
    # reports UNKNOWN_TOPIC_OR_PART while topic auto-creation propagates.
    producer.produce(dead_letter_topic, key="seed", value=b'{"seed":true}')
    assert producer.flush(10) == 0

    source_consumer = _consumer(source_topic, f"{prefix}-source-group")
    dlq_consumer = _consumer(dead_letter_topic, f"{prefix}-dlq-group")
    runtime = RedpandaAgentRuntime(
        object(),  # Mapping fails before Agent processing for malformed bytes.
        producer=RedpandaProducer({"bootstrap.servers": BOOTSTRAP}),
        dead_letter_topic=dead_letter_topic,
    )
    dispatcher = RedpandaEventDispatcher(
        source_consumer,
        terminal_failure_handler=runtime.route_terminal_failure,
    )
    try:
        deadline = time.monotonic() + 15.0
        dlq_records: list[dict] = []
        while time.monotonic() < deadline:
            dispatcher.dispatch(timeout=0.5)
            dlq_records.extend(
                record for record in dlq_consumer.poll(timeout=0.5) if "_failure" in record
            )
            if dlq_records:
                break
        assert len(dlq_records) == 1
        assert dlq_records[0]["_decode_failure"]["raw_value_base64"] == "/25vdC1qc29u"
        assert dlq_records[0]["_failure"]["terminal"] is True

        # The source dispatcher has committed the terminal record, so another
        # poll from the same group observes no replay of the poison bytes.
        assert source_consumer.poll(timeout=0.5) == []
    finally:
        source_consumer.close()
        dlq_consumer.close()


def test_oversized_broker_bytes_reach_bounded_dlq_once_and_source_offset_advances() -> None:
    """Oversized ingress is terminal, but the DLQ never copies its payload."""
    prefix = f"agent-oversized-dlq-{uuid.uuid4().hex}"
    source_topic, dead_letter_topic = f"{prefix}-source", f"{prefix}-dlq"
    raw_value = b"x" * 33
    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    producer.produce(source_topic, key="too-large-1", value=raw_value)
    producer.produce(dead_letter_topic, key="seed", value=b'{"seed":true}')
    assert producer.flush(10) == 0

    source_consumer = _consumer(source_topic, f"{prefix}-source-group", max_record_bytes=32)
    dlq_consumer = _consumer(dead_letter_topic, f"{prefix}-dlq-group")
    runtime = RedpandaAgentRuntime(
        object(),
        producer=RedpandaProducer({"bootstrap.servers": BOOTSTRAP}),
        dead_letter_topic=dead_letter_topic,
    )
    dispatcher = RedpandaEventDispatcher(source_consumer, terminal_failure_handler=runtime.route_terminal_failure)
    try:
        deadline = time.monotonic() + 15.0
        dlq_records: list[dict] = []
        while time.monotonic() < deadline:
            dispatcher.dispatch(timeout=0.5)
            dlq_records.extend(record for record in dlq_consumer.poll(timeout=0.5) if "_failure" in record)
            if dlq_records:
                break
        assert len(dlq_records) == 1
        diagnostic = dlq_records[0]["_decode_failure"]
        assert diagnostic["type"] == "RecordTooLarge"
        assert diagnostic["size_bytes"] == len(raw_value)
        assert diagnostic["sha256"] == hashlib.sha256(raw_value).hexdigest()
        assert diagnostic["payload_omitted"] is True
        assert "raw_value_base64" not in diagnostic
        assert dlq_records[0]["_failure"]["terminal"] is True
        assert source_consumer.poll(timeout=0.5) == []
    finally:
        source_consumer.close()
        dlq_consumer.close()
