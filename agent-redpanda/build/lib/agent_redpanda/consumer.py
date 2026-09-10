from __future__ import annotations

import json
from typing import Any

try:
    from confluent_kafka import Consumer, TopicPartition
except ImportError:  # pragma: no cover - for tests without the dependency installed
    Consumer = None
    TopicPartition = None


class RedpandaConsumer:
    def __init__(self, config: dict[str, Any] | None = None, *, topics: list[str] | None = None) -> None:
        self.config = dict(config or {})
        # Dispatcher acknowledgement controls the at-least-once boundary.
        self.config.setdefault("enable.auto.commit", False)
        self.topics = topics or []
        if Consumer is None:
            raise RuntimeError("confluent-kafka is required for RedpandaConsumer")
        self._consumer = Consumer(self.config)
        if self.topics:
            self._consumer.subscribe(self.topics)

    def poll(self, timeout: float = 1.0) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        message = self._consumer.poll(timeout)
        if message is None:
            return records
        if message.error():
            raise RuntimeError(f"Redpanda consumer error: {message.error()}")
        value = message.value()
        if value is None:
            return records
        record = json.loads(value.decode("utf-8"))
        if not isinstance(record, dict):
            raise TypeError("Redpanda record must decode to a dictionary")
        headers = self._decode_headers(message.headers() or [])
        self._extract_trace_context(headers)
        metadata = record.setdefault("metadata", {})
        if isinstance(metadata, dict):
            for name in ("traceparent", "tracestate"):
                if name in headers:
                    metadata.setdefault(name, headers[name])
        record["_transport"] = {
            "topic": message.topic(),
            "partition": message.partition(),
            "offset": message.offset(),
            "key": self._decode_key(message.key()),
            "headers": headers,
        }
        records.append(record)
        return records

    @staticmethod
    def _decode_headers(headers: list[tuple[str, bytes | str | None]]) -> dict[str, str]:
        return {
            key: value.decode("utf-8") if isinstance(value, bytes) else value
            for key, value in headers
            if isinstance(value, (bytes, str))
        }

    @staticmethod
    def _decode_key(value: bytes | str | None) -> str | None:
        return value.decode("utf-8") if isinstance(value, bytes) else value

    @staticmethod
    def _extract_trace_context(headers: dict[str, str]) -> None:
        try:
            from opentelemetry.propagate import extract
        except ImportError:
            return
        extract(headers)

    def close(self) -> None:
        self._consumer.close()

    def commit(self, record: dict[str, Any]) -> None:
        """Acknowledge a record after successful handler dispatch."""
        transport = record.get("_transport")
        if not isinstance(transport, dict):
            raise ValueError("Redpanda record has no transport offset to commit")
        topic = transport.get("topic")
        partition = transport.get("partition")
        offset = transport.get("offset")
        if not isinstance(topic, str) or not isinstance(partition, int) or not isinstance(offset, int):
            raise ValueError("Redpanda record has an invalid transport offset")
        self._consumer.commit(
            offsets=[TopicPartition(topic, partition, offset + 1)],
            asynchronous=False,
        )
