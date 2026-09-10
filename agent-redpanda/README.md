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

The 2S reconciliation integration tests are opt-in. Start the repository's
single-node local broker, install this package's dependencies, and run them:

```bash
docker compose -f docker-compose.redpanda.yml up -d
pip install -e ./agent-redpanda
pip install -e ./agent-delta
REDPANDA_BOOTSTRAP_SERVERS=localhost:19092 pytest -q agent-redpanda/tests/test_reconciliation_integration.py
```

They use the ordinary `inventory.reservation.reconciled` event boundary and
exercise redelivery after a handler failure, reconciliation sequence ordering,
lineage across consumer restart, and one keyed Delta write across a
consumer-group ownership handoff. All four assertions have passed against the
local Redpanda broker. Broker unavailability remains a separate, unverified
scenario.

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
