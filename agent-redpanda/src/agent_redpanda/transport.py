"""Generic Event transport and dispatcher backed by Redpanda topics."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Protocol

from agent_core import Event

from .consumer import RedpandaConsumer
from .mapper import RedpandaEventMapper
from .producer import RedpandaProducer


class TopicResolver(Protocol):
    def __call__(self, event: Event) -> str:
        ...


class RecordConsumer(Protocol):
    def poll(self, timeout: float = 1.0) -> list[dict]:
        ...

    def commit(self, record: dict) -> None:
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
    ) -> None:
        self.consumer = consumer
        self.mapper = mapper or RedpandaEventMapper()
        self._handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self._handlers[event_type].append(handler)

    def dispatch(self, timeout: float = 1.0) -> int:
        delivered = 0
        for record in self.consumer.poll(timeout):
            event = self.mapper.from_record(record)
            for handler in self._handlers[event.event_type]:
                handler(event)
                delivered += 1
            commit = getattr(self.consumer, "commit", None)
            if callable(commit):
                # If a handler raised, this acknowledgement is skipped and
                # the consumer group may redeliver the record.
                commit(record)
        return delivered
