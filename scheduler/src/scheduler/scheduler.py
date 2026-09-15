from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock, Timer
from typing import Protocol
import uuid

from agent_core import Event


class EventPublisher(Protocol):
    def publish(self, event: Event) -> None:
        ...


@dataclass(frozen=True)
class ScheduledEvent:
    schedule_id: str
    event: Event
    due_at: datetime


class InMemoryEventScheduler:
    """Publish delayed events through an existing transport.

    This adapter does not claim durability: a process restart loses timers.
    Domains should make timeout events idempotent through their normal event
    and operation contracts.
    """

    def __init__(self, publisher: EventPublisher) -> None:
        self.publisher = publisher
        self._timers: dict[str, Timer] = {}
        self._lock = Lock()

    def schedule(self, event: Event, *, delay: timedelta) -> ScheduledEvent:
        if delay.total_seconds() < 0:
            raise ValueError("delay must not be negative")
        schedule_id = str(uuid.uuid4())
        due_at = datetime.now(timezone.utc) + delay
        timer = Timer(delay.total_seconds(), self._publish, args=(schedule_id, event))
        timer.daemon = True
        with self._lock:
            self._timers[schedule_id] = timer
        timer.start()
        return ScheduledEvent(schedule_id=schedule_id, event=event, due_at=due_at)

    def schedule_timeout(
        self,
        *,
        event_type: str,
        entity_id: str,
        entity_type: str,
        operation_id: str,
        delay: timedelta,
        correlation_id: str | None = None,
    ) -> ScheduledEvent:
        metadata = {"operation_id": operation_id}
        if correlation_id:
            metadata["correlation_id"] = correlation_id
        return self.schedule(
            Event(
                event_type=event_type,
                entity_id=entity_id,
                entity_type=entity_type,
                payload={"operation_id": operation_id},
                source="scheduler",
                metadata=metadata,
                idempotency_key=f"timeout:{operation_id}",
            ),
            delay=delay,
        )

    def cancel(self, schedule_id: str) -> bool:
        with self._lock:
            timer = self._timers.pop(schedule_id, None)
        if timer is None:
            return False
        timer.cancel()
        return True

    def _publish(self, schedule_id: str, event: Event) -> None:
        with self._lock:
            self._timers.pop(schedule_id, None)
        self.publisher.publish(event)
