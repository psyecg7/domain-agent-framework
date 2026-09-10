from datetime import timedelta

from agent_scheduler import InMemoryEventScheduler


class Publisher:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


def test_timeout_event_can_be_cancelled_before_delivery() -> None:
    publisher = Publisher()
    scheduler = InMemoryEventScheduler(publisher)

    scheduled = scheduler.schedule_timeout(
        event_type="order.reservation.timeout",
        entity_id="ORD-1",
        entity_type="order",
        operation_id="RES-1",
        correlation_id="C1",
        delay=timedelta(seconds=60),
    )

    assert scheduler.cancel(scheduled.schedule_id) is True
    assert publisher.events == []
    assert scheduled.event.idempotency_key == "timeout:RES-1"
