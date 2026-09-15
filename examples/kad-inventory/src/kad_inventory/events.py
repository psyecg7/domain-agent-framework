"""Kafka ports for the reference application's event projection."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from .contracts import OperationalEvent

logger = logging.getLogger(__name__)
INVENTORY_EVENTS_TOPIC = "dev-inventory-events"
INVENTORY_COMMANDS_TOPIC = "dev-inventory-commands"
ORDER_RESULTS_TOPIC = "dev-order-results"


class EventPublisher(Protocol):
    def publish(
        self,
        event: OperationalEvent,
        *,
        topic: str | None = None,
        key: str | None = None,
    ) -> None: ...


class KafkaEventPublisher:
    """Synchronous Kafka-compatible publisher used by the reference runtime."""

    def __init__(self, bootstrap_servers: str, *, topic: str = INVENTORY_EVENTS_TOPIC) -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("confluent-kafka is required for KafkaEventPublisher") from exc
        self._topic = topic
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish(
        self,
        event: OperationalEvent,
        *,
        topic: str | None = None,
        key: str | None = None,
    ) -> None:
        self._producer.produce(
            topic or self._topic,
            key=key.encode("utf-8") if key is not None else None,
            value=json.dumps(event, sort_keys=True).encode("utf-8"),
        )
        outstanding = self._producer.flush(5)
        if outstanding:
            raise RuntimeError(f"Kafka publish left {outstanding} event(s) undelivered")


class KafkaProjectionConsumer:
    """Background consumer that materializes the Inventory semantic projection.

    The consumer commits only after the projection handler returns.  Real
    deployments must choose a durable projection store and delivery/rebuild
    strategy appropriate to their broker and system of record.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        apply_event: Callable[[OperationalEvent], None],
        *,
        group_id: str = "dev-inventory-catalog",
        topic: str = INVENTORY_EVENTS_TOPIC,
    ) -> None:
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("confluent-kafka is required for KafkaProjectionConsumer") from exc
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([topic])
        self._apply_event = apply_event
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start_background(self) -> None:
        """Start the subscribed projection loop in a named daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Kafka projection consumer is already running")
        self._thread = threading.Thread(
            target=self.run_forever,
            name="kad-inventory-projection",
            daemon=True,
        )
        self._thread.start()

    def poll_once(self, timeout: float = 1.0) -> bool:
        message = self._consumer.poll(timeout)
        if message is None:
            return False
        if message.error():
            raise RuntimeError(f"Kafka consumer error: {message.error()}")
        try:
            event = json.loads(message.value().decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("Inventory event must be a JSON object")
            self._apply_event(event)
        except Exception:
            # An uncommitted record is deliberately eligible for redelivery.
            logger.exception("Inventory projection rejected broker event")
            raise
        self._consumer.commit(message=message, asynchronous=False)
        return True

    def run_forever(self) -> None:
        while not self._stopped:
            self.poll_once()

    def close(self) -> None:
        self._stopped = True
        self._consumer.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)


class KafkaInventoryCommandConsumer(KafkaProjectionConsumer):
    """Consume Inventory commands on a separate, inventory-keyed topic."""

    def __init__(
        self,
        bootstrap_servers: str,
        handle_command: Callable[[OperationalEvent], None],
        *,
        group_id: str = "dev-inventory-command-handler",
    ) -> None:
        super().__init__(
            bootstrap_servers,
            handle_command,
            group_id=group_id,
            topic=INVENTORY_COMMANDS_TOPIC,
        )
