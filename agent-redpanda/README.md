# agent-redpanda

`agent-redpanda` is a thin adapter that connects Redpanda to the generic `agent-core` runtime.

## Purpose

Redpanda is treated as an event transport adapter. It does not define business semantics, domain policies, or state models.

The separation is deliberately kept simple:

- `agent-core` owns the runtime lifecycle and generic agent semantics.
- `agent-redpanda` owns the conversion between Redpanda records and the generic `agent_core.Event` model.
- domain-specific policies, decisions, and actions live in the application layer that uses the runtime.

> Redpanda is an event transport adapter. `agent-core` is the domain-agent runtime. The core has no dependency on Redpanda or Kafka.

## Architecture

The adapter flow is:

Redpanda record -> mapper -> `agent_core.Event` -> `Agent.process()` -> `AgentResult` -> producer -> Redpanda record

This keeps the core reusable if the transport changes from Redpanda to another broker later.

## Event transport boundary

`RedpandaEventTransport` implements the generic `publish(Event)` shape used by
capability invocation. It serializes an Event through `RedpandaEventMapper` and
publishes it to an application-selected topic. `RedpandaEventDispatcher`
consumes records and invokes explicit event-type subscribers. Neither class
performs local routing, retries business operations, or turns a broker failure
into a domain result.

`RedpandaConsumer` disables automatic offset commits by default. The dispatcher
commits only after all subscribed handlers complete successfully; a handler
failure leaves the offset uncommitted for broker redelivery. This is an
at-least-once delivery posture, not exactly-once business execution.

The consumer fetches up to `batch_size` records per poll (default `100`). On a
successful batch, it performs one synchronous commit containing the highest
handled offset for each partition. If any handler fails, the batch is left
uncommitted rather than committing past the failure. This favors safe
redelivery over throughput during faults; handlers still need idempotency.

When `RedpandaAgentRuntime.process_record()` routes a malformed terminal
record to a configured dead-letter topic, it returns `None` only after the DLQ
producer accepts that copy. A consumer loop may then commit the source record;
without a successful DLQ route it must leave the offset uncommitted. Retryable
failures still raise for ordinary broker redelivery. Raw non-JSON broker bytes
remain a consumer-boundary deployment concern and must be routed through this
same terminal-DLQ path rather than decoded outside it. For direct dispatcher
use, pass `terminal_failure_handler=runtime.route_terminal_failure`; the
dispatcher commits a malformed source record only when that callback returns
`True` after the DLQ publish.

`RedpandaConsumer` applies a 1 MiB ingress limit by default. Configure
`max_record_bytes` for a domain-specific limit. A larger record becomes a
terminal `RecordTooLarge` diagnostic containing only its byte count and
SHA-256 digest—never a base64 copy of the original payload. As with other
terminal failures, the source offset is committed only after the DLQ publisher
accepts that bounded diagnostic.

```python
consumer = RedpandaConsumer(
    {"bootstrap.servers": "localhost:9092", "group.id": "orders"},
    topics=["orders"],
    max_record_bytes=256 * 1024,
)
```

## Dispatcher observability

`RedpandaEventDispatcher` accepts optional lifecycle observers. One payload-free
record is emitted for each completed poll: `succeeded` or `failed`, with poll,
delivery, acknowledgement, terminal-DLQ counts, duration, and an error type
when applicable.

```python
def metrics(batch):
    print(batch.phase, batch.polled_records, batch.acknowledged_records)

dispatcher = RedpandaEventDispatcher(consumer, observers=(metrics,))
```

Observers are best-effort. An observer exception is logged but cannot commit an
offset, suppress a handler error, or otherwise change at-least-once delivery.
No broker record payload is included in the lifecycle object. Exporting logs or
metrics to OpenTelemetry, Prometheus, or another platform remains deployment
configuration rather than an adapter dependency.

The reconciliation integration tests run in the repository's service-backed
CI workflow. To reproduce them locally, start the repository's Compose stack
(it includes Redpanda and PostgreSQL), install this package's dependencies, and
run them:

```bash
docker compose -f docker-compose.redpanda.yml up -d
pip install -e ./agent-redpanda
pip install -e ./agent-delta
REDPANDA_BOOTSTRAP_SERVERS=localhost:19092 pytest -q agent-redpanda/tests/test_reconciliation_integration.py
```

To exercise only the broker, use `up -d redpanda`; to also run the PostgreSQL
atomicity integration test, use `up -d postgres` and follow
[`agent-postgres`](../agent-postgres/README.md).

They use the ordinary `inventory.reservation.reconciled` event boundary and
exercise redelivery after a handler failure, reconciliation sequence ordering,
lineage across consumer restart, and one keyed Delta write across a
consumer-group ownership handoff. The suite also publishes malformed non-JSON
bytes and oversized bytes, proving that one terminal DLQ record is written
before the source offset advances and that an oversized diagnostic does not
copy its payload. All six assertions have passed against the local Redpanda
broker. Broker unavailability remains a separate, unverified scenario.

### Manual broker-recovery check

The Compose service persists Redpanda data in the named `redpanda-data` volume.
The following explicitly restarts the local broker after an uncommitted Order
observation, then verifies that the same record is redelivered and applied only
once. It is intentionally outside normal pytest runs because it interrupts the
local broker:

```bash
REDPANDA_CHAOS_TESTS=1 REDPANDA_BOOTSTRAP_SERVERS=localhost:19092 \
  python scripts/redpanda_recovery_check.py
```

`RedpandaProducer` also raises if its flush leaves records undelivered, so a
broker outage cannot be reported as a successful publish. The recovery check
has passed against the local broker; transient `librdkafka` disconnect logs are
expected while the container restarts.

This skipped integration is explicit verification debt. A broker validation
must assert redelivery after handler failure, correct reconciliation outcome
under deliberately reordered sequences, and unchanged correlation/causation
lineage through consumer restart or rebalance; a passing process exit alone is
not sufficient.

## Installation

```bash
pip install -e ./agent-redpanda
```

## Configuration

The adapter is intentionally small. A minimal configuration can include:

- consumer bootstrap servers
- consumer group id
- topic names
- producer bootstrap servers
- serializer configuration

Example:

```python
config = {
    "bootstrap.servers": "localhost:9092",
    "group.id": "agent-runtime",
}
```

## Example producer

```python
from agent_redpanda import RedpandaProducer

producer = RedpandaProducer({"bootstrap.servers": "localhost:9092"})

producer.publish(
    topic="agent.decisions",
    key="order-77",
    value={
        "event_type": "decision",
        "entity_id": "order-77",
        "entity_type": "order",
        "decision_type": "HIGH_RISK_ORDER",
        "reason": "Risk threshold exceeded",
    },
)
```

## Example consumer

```python
from agent_redpanda import RedpandaConsumer

consumer = RedpandaConsumer(
    {
        "bootstrap.servers": "localhost:9092",
        "group.id": "agent-runtime",
    },
    topics=["agent.events"],
)

for record in consumer.poll():
    print(record)
```

## Mapping between Redpanda records and agent_core.Event

The mapper translates a Redpanda record into:

```python
Event(
    event_id=...,  # from record key or explicit metadata
    event_type=...,  # from payload field or config
    entity_id=...,  # from payload or config
    entity_type=...,  # from payload or config
    payload=...,  # the event payload
    occurred_at=...,  # timestamp from record or message time
    source=...,  # configured source or broker metadata
)
```

Missing required fields should fail explicitly rather than being inferred with domain assumptions.

## Current delivery semantics

This milestone intentionally does not promise transactional semantics or exactly-once processing.

Current semantics:

- records are produced as JSON messages
- mapping failures raise exceptions
- processing failures raise exceptions
- publishing failures raise exceptions
- duplicate delivery is not yet handled by an idempotency subsystem

## Deliberately not implemented

This milestone does not include:

- LangChain
- Ollama
- LLM reasoning
- Delta Lake
- LanceDB
- Schema Registry
- Avro
- distributed transactions
- exactly-once guarantees
- domain-specific agents or policies
- pharmacy, inventory, or PIM business logic

## Core principle

`agent-redpanda` is only an event transport adapter. The domain-agent runtime remains in `agent-core`, and the core remains independent from Redpanda and Kafka.
