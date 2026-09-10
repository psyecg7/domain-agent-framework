from agent_core import Event
import pytest

from agent_redpanda import RedpandaEventDispatcher, RedpandaEventTransport
from agent_redpanda.producer import RedpandaProducer


class Producer:
    def __init__(self) -> None:
        self.calls = []

    def publish(self, *, topic, key, value, headers=None) -> None:
        self.calls.append((topic, key, value, headers))


class Consumer:
    def __init__(self, records) -> None:
        self.records = records
        self.committed = []

    def poll(self, timeout=1.0):
        records, self.records = self.records, []
        return records

    def commit(self, record) -> None:
        self.committed.append(record)


class UnderlyingProducer:
    def __init__(self, undelivered: int) -> None:
        self.undelivered = undelivered
        self.published = []

    def produce(self, topic, **kwargs) -> None:
        self.published.append({"topic": topic, **kwargs})

    def flush(self) -> int:
        return self.undelivered


def test_event_transport_publishes_generic_event_with_lineage_and_trace_headers() -> None:
    producer = Producer()
    transport = RedpandaEventTransport(producer, topic_for_event=lambda event: "reconciliation.requests")
    event = Event(
        "capability.invocation",
        "process-1",
        "order_process",
        payload={"capability_id": "inventory.reservation.reconcile"},
        metadata={"correlation_id": "C1", "traceparent": "00-abc-def-01"},
    )

    transport.publish(event)

    topic, key, record, headers = producer.calls[0]
    assert topic == "reconciliation.requests"
    assert key == "order_process:process-1"
    assert record["metadata"]["correlation_id"] == "C1"
    assert headers == {"traceparent": "00-abc-def-01"}


def test_dispatcher_reconstructs_event_and_delivers_only_explicit_subscribers() -> None:
    producer = Producer()
    transport = RedpandaEventTransport(producer, topic_for_event=lambda event: "reconciliation.results")
    event = Event(
        "inventory.reservation.reconciled",
        "process-1",
        "order_process",
        payload={"reservation_status": "STILL_UNKNOWN"},
        metadata={"correlation_id": "C1", "causation_id": "request-1"},
    )
    transport.publish(event)
    received = []
    consumer = Consumer([producer.calls[0][2]])
    dispatcher = RedpandaEventDispatcher(consumer)
    dispatcher.subscribe("inventory.reservation.reconciled", received.append)

    assert dispatcher.dispatch() == 1
    assert received == [event]
    assert consumer.committed == [producer.calls[0][2]]


def test_dispatcher_does_not_commit_when_a_handler_fails() -> None:
    record = {
        "event_id": "evt-1",
        "event_type": "inventory.reservation.reconciled",
        "entity_id": "process-1",
        "entity_type": "order_process",
        "occurred_at": "2024-01-01T00:00:00+00:00",
    }
    consumer = Consumer([record])
    dispatcher = RedpandaEventDispatcher(consumer)

    def fail(event):
        raise RuntimeError("inventory handler failed")

    dispatcher.subscribe("inventory.reservation.reconciled", fail)

    try:
        dispatcher.dispatch()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected handler failure")

    assert consumer.committed == []


def test_producer_fails_when_the_broker_leaves_records_undelivered() -> None:
    producer = object.__new__(RedpandaProducer)
    producer._producer = UnderlyingProducer(undelivered=1)

    with pytest.raises(RuntimeError, match="1 record"):
        producer.publish(topic="events", key="order:1", value={"event_type": "test"})
