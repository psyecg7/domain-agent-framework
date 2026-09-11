"""Generic Event transport and dispatcher backed by Redpanda topics."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Protocol
import logging
from threading import Lock
from time import perf_counter

from agent_core import Event

from .consumer import RedpandaConsumer
from .mapper import RedpandaEventMapper
from .producer import RedpandaProducer
from .observability import RedpandaDispatchEvent, now


logger = logging.getLogger(__name__)


class TopicResolver(Protocol):
    def __call__(self, event: Event) -> str:
        ...


class RecordConsumer(Protocol):
    def poll(self, timeout: float = 1.0) -> list[dict]:
        ...

    def commit(self, record: dict) -> None:
        ...

    def commit_many(self, records: list[dict]) -> None:
        ...


class RedpandaEventTransport:
    """Publish generic Events without exposing Redpanda to domain code.

    Topic selection is application configuration. This transport does not route
    handlers locally, retry operations, or infer domain outcomes.
    """

    def __init__(
        self,
        producer: RedpandaProducer,
        *,
        topic_for_event: TopicResolver,
        mapper: RedpandaEventMapper | None = None,
    ) -> None:
        self.producer = producer
        self.topic_for_event = topic_for_event
        self.mapper = mapper or RedpandaEventMapper()

    def publish(self, event: Event) -> None:
        topic = self.topic_for_event(event)
        if not topic:
            raise ValueError(f"No topic configured for {event.event_type}")
        headers = {
            key: value
            for key in ("traceparent", "tracestate")
            if isinstance((value := event.metadata.get(key)), str) and value
        }
        self.producer.publish(
            topic=topic,
            key=f"{event.entity_type}:{event.entity_id}",
            value=self.mapper.to_record(event),
            headers=headers,
        )


class RedpandaEventDispatcher:
    """Deliver broker records to explicit event-type subscribers."""

    def __init__(
        self,
        consumer: RecordConsumer | RedpandaConsumer,
        *,
        mapper: RedpandaEventMapper | None = None,
        terminal_failure_handler: Callable[[dict, Exception], bool] | None = None,
        observers: tuple[Callable[[RedpandaDispatchEvent], None], ...] = (),
    ) -> None:
        self.consumer = consumer
        self.mapper = mapper or RedpandaEventMapper()
        self.terminal_failure_handler = terminal_failure_handler
        self._observers = list(observers)
        if not all(callable(observer) for observer in self._observers):
            raise TypeError("observers must be callable")
        self._observer_lock = Lock()
        self._handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self._handlers[event_type].append(handler)

    def observe(self, observer: Callable[[RedpandaDispatchEvent], None]) -> Callable[[RedpandaDispatchEvent], None]:
        """Register a best-effort payload-free dispatcher lifecycle observer."""
        if not callable(observer):
            raise TypeError("observer must be callable")
        with self._observer_lock:
            self._observers.append(observer)
        return observer

    def dispatch(self, timeout: float = 1.0) -> int:
        started_at = perf_counter()
        delivered = 0
        terminal = 0
        acknowledged: list[dict] = []
        records: list[dict] = []
        try:
            records = self.consumer.poll(timeout)
            for record in records:
                try:
                    event = self.mapper.from_record(record)
                except (TypeError, ValueError) as exc:
                    # Malformed transport/schema input is terminal only if the
                    # configured router accepted a DLQ copy. In that one case the
                    # source offset may advance; otherwise it remains retryable.
                    if self.terminal_failure_handler is None or not self.terminal_failure_handler(record, exc):
                        raise
                    acknowledged.append(record)
                    terminal += 1
                    continue
                for handler in self._handlers[event.event_type]:
                    handler(event)
                    delivered += 1
                acknowledged.append(record)
            commit_many = getattr(self.consumer, "commit_many", None)
            if callable(commit_many):
                # If any handler raised above, this acknowledgement is skipped for
                # the whole batch. The broker may redeliver prior records, which is
                # safe under the documented at-least-once contract.
                commit_many(acknowledged)
            else:
                commit = getattr(self.consumer, "commit", None)
                if callable(commit):
                    for record in acknowledged:
                        commit(record)
        except Exception as exc:
            self._emit(
                "failed", started_at, len(records), delivered, len(acknowledged), terminal, type(exc).__name__,
            )
            raise
        self._emit("succeeded", started_at, len(records), delivered, len(acknowledged), terminal)
        return delivered

    def _emit(
        self,
        phase: str,
        started_at: float,
        polled_records: int,
        delivered_handlers: int,
        acknowledged_records: int,
        terminal_records: int,
        error_type: str | None = None,
    ) -> None:
        lifecycle = RedpandaDispatchEvent(
            phase=phase, occurred_at=now(), duration_ms=(perf_counter() - started_at) * 1_000,
            polled_records=polled_records, delivered_handlers=delivered_handlers,
            acknowledged_records=acknowledged_records, terminal_records=terminal_records,
            error_type=error_type,
        )
        logger.info("agent_redpanda.dispatch.%s", phase, extra={"redpanda_dispatch": lifecycle.__dict__})
        with self._observer_lock:
            observers = tuple(self._observers)
        for observer in observers:
            try:
                observer(lifecycle)
            except Exception:
                logger.exception("agent_redpanda.dispatch_observer_failed")
