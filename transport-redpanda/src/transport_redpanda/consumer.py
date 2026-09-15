from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

try:
    from confluent_kafka import Consumer, TopicPartition
except ImportError:  # pragma: no cover - for tests without the dependency installed
    Consumer = None
    TopicPartition = None


class RedpandaConsumer:
    """Decode broker records with a bounded diagnostic path for poison bytes."""

    DEFAULT_MAX_RECORD_BYTES = 1_048_576

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        topics: list[str] | None = None,
        batch_size: int = 100,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    ) -> None:
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if not isinstance(max_record_bytes, int) or isinstance(max_record_bytes, bool) or max_record_bytes < 1:
            raise ValueError("max_record_bytes must be a positive integer")
        self.config = dict(config or {})
        # Dispatcher acknowledgement controls the at-least-once boundary.
        self.config.setdefault("enable.auto.commit", False)
        self.topics = topics or []
        self.batch_size = batch_size
        self.max_record_bytes = max_record_bytes
        if Consumer is None:
            raise RuntimeError("confluent-kafka is required for RedpandaConsumer")
        self._consumer = Consumer(self.config)
        if self.topics:
            self._consumer.subscribe(self.topics)

    def poll(self, timeout: float = 1.0) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        messages = self._consumer.consume(num_messages=self.batch_size, timeout=timeout)
        for message in messages:
            if message is None:
                continue
            if message.error():
                raise RuntimeError(f"Redpanda consumer error: {message.error()}")
            value = message.value()
            if value is None:
                continue
            records.append(self._record_from_message(message, value))
        return records

    def _record_from_message(self, message: Any, value: bytes) -> dict[str, Any]:
        headers = self._decode_headers(message.headers() or [])
        self._extract_trace_context(headers)
        transport = {
            "topic": message.topic(),
            "partition": message.partition(),
            "offset": message.offset(),
            "key": self._decode_key(message.key()),
            "headers": headers,
        }
        if len(value) > self.max_record_bytes:
            # Do not base64 encode an arbitrarily large broker record into a
            # DLQ diagnostic. The terminal route gets enough stable evidence
            # to correlate the source record without duplicating its contents.
            return {
                "_decode_failure": {
                    "type": "RecordTooLarge",
                    "message": f"Redpanda record exceeds {self.max_record_bytes} byte limit",
                    "size_bytes": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "payload_omitted": True,
                },
                "_transport": transport,
            }
        try:
            record = json.loads(value.decode("utf-8"))
            if not isinstance(record, dict):
                raise TypeError("Redpanda record must decode to a dictionary")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            # Preserve the unparseable broker payload for a terminal failure
            # router. Do not throw before DLQ handling gets a chance to make a
            # durable diagnostic copy and acknowledge the source offset.
            return {
                "_decode_failure": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "raw_value_base64": base64.b64encode(value).decode("ascii"),
                },
                "_transport": transport,
            }
        metadata = record.setdefault("metadata", {})
        if isinstance(metadata, dict):
            for name in ("traceparent", "tracestate"):
                if name in headers:
                    metadata.setdefault(name, headers[name])
        record["_transport"] = transport
        return record

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
        self.commit_many([record])

    def commit_many(self, records: list[dict[str, Any]]) -> None:
        """Synchronously commit the highest completed offset per partition.

        The caller must pass only a contiguous successfully handled prefix for
        every partition. This preserves at-least-once delivery while avoiding
        one broker round trip per message on successful batches.
        """
        offsets: dict[tuple[str, int], int] = {}
        for record in records:
            transport = record.get("_transport")
            if not isinstance(transport, dict):
                raise ValueError("Redpanda record has no transport offset to commit")
            topic = transport.get("topic")
            partition = transport.get("partition")
            offset = transport.get("offset")
            if not isinstance(topic, str) or not isinstance(partition, int) or not isinstance(offset, int):
                raise ValueError("Redpanda record has an invalid transport offset")
            key = (topic, partition)
            offsets[key] = max(offsets.get(key, -1), offset + 1)
        if not offsets:
            return
        self._consumer.commit(
            offsets=[TopicPartition(topic, partition, offset) for (topic, partition), offset in sorted(offsets.items())],
            asynchronous=False,
        )
