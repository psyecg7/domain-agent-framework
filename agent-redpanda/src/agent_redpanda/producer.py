from __future__ import annotations

import json
from typing import Any

try:
    from confluent_kafka import Producer
except ImportError:  # pragma: no cover - for tests without the dependency installed
    Producer = None


class RedpandaProducer:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        if Producer is None:
            raise RuntimeError("confluent-kafka is required for RedpandaProducer")
        self._producer = Producer(self.config)

    def publish(
        self,
        *,
        topic: str,
        key: str | None,
        value: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not topic:
            raise ValueError("topic is required")
        serialized = json.dumps(value, default=str).encode("utf-8")
        carrier = dict(headers or {})
        self._inject_trace_context(carrier)
        self._producer.produce(topic, key=key, value=serialized, headers=carrier or None)
        undelivered = self._producer.flush()
        if undelivered:
            raise RuntimeError(f"Redpanda publish left {undelivered} record(s) undelivered")

    @staticmethod
    def _inject_trace_context(carrier: dict[str, str]) -> None:
        """Inject W3C trace headers when OpenTelemetry is installed."""
        try:
            from opentelemetry.propagate import inject
        except ImportError:
            return
        inject(carrier)
