# scheduler

`scheduler` provides `InMemoryEventScheduler`, a small local timer that
publishes an Event after a delay.

```text
schedule an Event → timer waits → existing publisher receives the Event
```

Use it for a local prototype or test. It is not durable: restarting the Python
process loses pending timers. A production scheduler needs its own durable
storage, delivery, duplicate-handling, and recovery contract.

## Usage

```python
from datetime import timedelta
from scheduler import InMemoryEventScheduler

scheduler = InMemoryEventScheduler(publisher)
scheduler.schedule(event, delay=timedelta(minutes=5))
```

`schedule_timeout()` creates an Event with a stable timeout idempotency key
from an operation ID. The receiving domain still decides what a timeout means;
publishing a timeout Event never retries, compensates, or changes business
state by itself.
