import hashlib

from agent_core import Event
import pytest

from agent_redpanda import RedpandaConsumer, RedpandaEventDispatcher, RedpandaEventTransport
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


class MalformedMessage:
    def error(self): return None
    def value(self): return b"\xffnot-json"
    def headers(self): return []
    def topic(self): return "orders"
    def partition(self): return 2
    def offset(self): return 9
    def key(self): return b"order:1"


class UnderlyingConsumer:
    def consume(self, num_messages, timeout): return [MalformedMessage()]


class OversizedMessage(MalformedMessage):
    def value(self): return b"x" * 33


class OversizedUnderlyingConsumer:
    def consume(self, num_messages, timeout): return [OversizedMessage()]


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


def test_dispatcher_commits_a_malformed_record_only_after_terminal_router_accepts_it() -> None:
    malformed = {"_decode_failure": {"raw_value_base64": "bm90LWpzb24="}}
    consumer = Consumer([malformed])
    routed = []
    dispatcher = RedpandaEventDispatcher(
        consumer,
        terminal_failure_handler=lambda record, error: routed.append((record, error)) is None,
    )

    assert dispatcher.dispatch() == 0
    assert routed[0][0] == malformed
    assert isinstance(routed[0][1], ValueError)
    assert consumer.committed == [malformed]


def test_dispatcher_leaves_malformed_record_uncommitted_when_dlq_router_fails() -> None:
    malformed = {"_decode_failure": {"raw_value_base64": "bm90LWpzb24="}}
    consumer = Consumer([malformed])
    dispatcher = RedpandaEventDispatcher(consumer, terminal_failure_handler=lambda record, error: False)

    with pytest.raises(ValueError, match="event_type"):
        dispatcher.dispatch()
    assert consumer.committed == []


def test_consumer_preserves_non_json_bytes_for_terminal_failure_routing() -> None:
    consumer = object.__new__(RedpandaConsumer)
    consumer._consumer = UnderlyingConsumer()
    consumer.batch_size = 100
    consumer.max_record_bytes = RedpandaConsumer.DEFAULT_MAX_RECORD_BYTES
    records = consumer.poll()

    assert records[0]["_decode_failure"]["raw_value_base64"] == "/25vdC1qc29u"
    assert records[0]["_transport"]["topic"] == "orders"


def test_consumer_bounds_oversized_record_dlq_diagnostic() -> None:
    consumer = object.__new__(RedpandaConsumer)
    consumer._consumer = OversizedUnderlyingConsumer()
    consumer.batch_size = 100
    consumer.max_record_bytes = 32

    record = consumer.poll()[0]

    diagnostic = record["_decode_failure"]
    assert diagnostic == {
        "type": "RecordTooLarge",
        "message": "Redpanda record exceeds 32 byte limit",
        "size_bytes": 33,
        "sha256": hashlib.sha256(b"x" * 33).hexdigest(),
        "payload_omitted": True,
    }
    assert "raw_value_base64" not in diagnostic
    assert record["_transport"]["topic"] == "orders"


def test_dispatcher_batches_safe_acknowledgements_per_consumer_poll() -> None:
    records = [
        {"event_id": "one", "event_type": "event", "entity_id": "1", "entity_type": "x", "occurred_at": "2024-01-01T00:00:00+00:00"},
        {"event_id": "two", "event_type": "event", "entity_id": "2", "entity_type": "x", "occurred_at": "2024-01-01T00:00:00+00:00"},
    ]

    class BatchConsumer(Consumer):
        def __init__(self):
            super().__init__(records)
            self.batches = []

        def commit_many(self, handled):
            self.batches.append(handled)

    consumer = BatchConsumer()
    dispatcher = RedpandaEventDispatcher(consumer)
    received = []
    dispatcher.subscribe("event", received.append)

    assert dispatcher.dispatch() == 2
    assert [event.event_id for event in received] == ["one", "two"]
    assert [[record["event_id"] for record in batch] for batch in consumer.batches] == [["one", "two"]]


def test_dispatcher_emits_payload_free_batch_lifecycle_observation() -> None:
    record = {"event_id": "one", "event_type": "event", "entity_id": "1", "entity_type": "x", "occurred_at": "2024-01-01T00:00:00+00:00"}
    observed = []
    dispatcher = RedpandaEventDispatcher(Consumer([record]), observers=(observed.append,))
    dispatcher.subscribe("event", lambda _: None)

    assert dispatcher.dispatch() == 1
    assert len(observed) == 1
    lifecycle = observed[0]
    assert lifecycle.phase == "succeeded"
    assert lifecycle.polled_records == lifecycle.acknowledged_records == 1
    assert lifecycle.delivered_handlers == 1
    assert lifecycle.terminal_records == 0
    assert not hasattr(lifecycle, "payload")


def test_dispatcher_observer_failure_does_not_change_acknowledgement() -> None:
    record = {"event_id": "one", "event_type": "event", "entity_id": "1", "entity_type": "x", "occurred_at": "2024-01-01T00:00:00+00:00"}

    def broken(_):
        raise RuntimeError("metrics unavailable")

    consumer = Consumer([record])
    dispatcher = RedpandaEventDispatcher(consumer, observers=(broken,))
    dispatcher.subscribe("event", lambda _: None)

    assert dispatcher.dispatch() == 1
    assert consumer.committed == [record]


def test_dispatcher_reports_handler_failure_without_acknowledging() -> None:
    record = {"event_id": "one", "event_type": "event", "entity_id": "1", "entity_type": "x", "occurred_at": "2024-01-01T00:00:00+00:00"}
    observed = []
    consumer = Consumer([record])
    dispatcher = RedpandaEventDispatcher(consumer, observers=(observed.append,))
    dispatcher.subscribe("event", lambda _: (_ for _ in ()).throw(RuntimeError("handler failed")))

    with pytest.raises(RuntimeError, match="handler failed"):
        dispatcher.dispatch()
    assert observed[0].phase == "failed"
    assert observed[0].error_type == "RuntimeError"
    assert observed[0].acknowledged_records == 0
    assert consumer.committed == []


def test_producer_fails_when_the_broker_leaves_records_undelivered() -> None:
    producer = object.__new__(RedpandaProducer)
    producer._producer = UnderlyingProducer(undelivered=1)

    with pytest.raises(RuntimeError, match="1 record"):
        producer.publish(topic="events", key="order:1", value={"event_type": "test"})
