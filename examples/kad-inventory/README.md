# Knowledge-aware Inventory reference

This is a small, runnable application reference—not an extension to
`agent-core`. It demonstrates a useful boundary for AI-assisted Inventory:

1. `POST /evaluate-alternative` reads a materialized Inventory projection and
   returns a typed, authoritative evaluation plus the exact state version it
   observed.
2. An AI or caller can propose a `MoveOrderIntent`, but `POST /execute-intent`
   compiles it into a guarded domain command.
3. Inventory compares that expected version with the live version inside its
   atomic domain transition. A mismatch returns `409`, emits
   `ReallocationFailed`, and leaves stock unchanged.

The included test deliberately evaluates `SKU-992` at 40 units/version 1,
then simulates a customer consuming all stock and advancing the version to 2.
The stale proposed move is rejected. This is a deterministic proof of the
evaluation-to-execution boundary; it does not claim that an LLM itself is
reliable or authoritative.

## Run the reference test

```bash
python -m pip install -e './examples/kad-inventory[test]'
pytest -q examples/kad-inventory/tests
```

## Kafka event/result transport

Kafka carries the durable Inventory events and Order results. It is not
synchronous request/reply and it is not the preferred command ingress in this
reference; use the gRPC stream below for that. Kafka topics are transport
lanes, not the mechanism that identifies an Order. Every message includes four
durable lineage fields:

| Field | Purpose |
|---|---|
| `event_id` | Identifies one delivered record and makes redelivery detectable. |
| `correlation_id` | Identifies the complete Order journey, for example `order:ORD-992`. |
| `causation_id` | Identifies the immediately preceding request record. |
| `operation_id` + `attempt_id` | Identifies one business operation and rejects a late previous attempt. |

The KAD reference uses environment-prefixed, versionable topic names:

| Topic | Kafka key | Why |
|---|---|---|
| `dev-inventory-commands` | `SKU|warehouse` | Legacy event-path demonstration; do not combine it with gRPC for one operation. |
| `dev-order-results` | `order_id` | Updates for one Order process share a partition. |
| `dev-inventory-events` | domain-defined Inventory key | Rebuilds the read projection. |

`OrderInventoryCoordinator` writes a pending Order process and its outbound
Inventory request before publication. The saved process, not an in-memory
request/reply map, correlates a later result. It therefore survives a process
restart; duplicate result `event_id`s are ignored, while a result for a
different operation attempt is rejected. The included tests deliver two
results in reverse order and then reconstruct an Order coordinator from disk.

`submit_many()` exists to retain the original Kafka command-path demonstration.
It accepts a burst of Orders, records all their pending processes, and
publishes bounded, broker-acknowledged batches. It returns before any Inventory
result exists. New deployments should persist through `persist_many()` and
use `GrpcOrderCommandDispatcher` below instead.

Use `PostgresOrderProcessStore` for any shared or production-like deployment.
It commits the Order process and its outbox request in one PostgreSQL
transaction, and its `claim_pending()` operation uses `FOR UPDATE SKIP LOCKED`
to lease work to exactly one publisher replica at a time. A crash before Kafka
acknowledgement leaves the stable request eligible for later republishing.

`SqliteOrderProcessStore` remains only for the no-infrastructure local test.
It is not a recovery, failover, or multi-replica solution. Start every Order
publisher with `dispatch_pending()` so it claims any requests left after a
previous process crash.

Submitting the same `order_id` again resolves to the existing correlation
process and does not emit a second Inventory request. Treat `order_id` as the
stable client idempotency identity; a materially different order must receive
a new order ID rather than overwrite an in-flight operation.

Run the PostgreSQL Order outbox and Inventory command-inbox durability proofs
against the repository Compose service:

```bash
KAD_POSTGRES_DATABASE_URL='postgresql+psycopg://agent:agent@localhost:5432/agent_atomic' \
  pytest -q examples/kad-inventory/tests/test_postgres_order_protocol_integration.py
```

## Kafka runtime adapter

`KafkaProjectionConsumer` subscribes to the environment-prefixed
`dev-inventory-events` topic and commits offsets only after projection
application. `KafkaOrderResultConsumer` does so for `dev-order-results`.
Start multiple instances in the same consumer group to process result
partitions in parallel. Kafka preserves order only within a partition, so the
result key is `order_id` (serializing one Order process).
`KafkaInventoryCommandConsumer` remains solely for the legacy Kafka-command
demonstration. `KafkaEventPublisher` uses producer batching and waits for
broker acknowledgement once per outbox batch—not once per Order result. Start
consumers with `start_background()` during application startup and call
`close()` during shutdown. The tests use an in-memory publisher on purpose:
they prove lineage and recovery without requiring a broker.

The in-memory state is only a reference implementation. Production must use
the Inventory system of record's native conditional update and make effect
evidence durable according to the domain's reconciliation design.

## gRPC command ingress

For a high-throughput command path, use the versioned Protobuf contract in
[`protocol/kad/inventory-command-v1.proto`](../../protocol/kad/inventory-command-v1.proto).
It is a bidirectional gRPC stream, not a REST endpoint and not synchronous
request/reply: one Order connection can submit many commands, and Inventory
returns `ACCEPTED` only after its command inbox has persisted each one.

```text
Order stream ── command × N ──> Inventory command inbox
Order stream <─ acceptance × N ─ Inventory command gateway

Inventory worker pool ── domain result event ──> Kafka/Redpanda ──> Order process
```

An `ACCEPTED` acknowledgement is **not** `InventoryReallocated`. It means only
that the target domain took durable responsibility for the command. Execution
remains asynchronous, with an explicit result event keyed by the same
`operation_id`, `attempt_id`, and `correlation_id`.

The included `InventoryCommandGateway` is an adapter around an
`InventoryCommandInbox` port. On the Order side,
`GrpcOrderCommandDispatcher` leases the existing durable Order outbox, submits
one leased batch through the stream, and marks each request sent only after its
`ACCEPTED` or `DUPLICATE` acknowledgement. Do not configure both the Kafka
command dispatcher and the gRPC dispatcher for the same outbox.

Its in-memory inbox is exclusively a test adapter. A deployed Inventory
service uses `PostgresInventoryCommandInbox` as the included shared-store
reference. It survives reconstruction, rejects a duplicate operation attempt,
and leases a command to one worker using `FOR UPDATE SKIP LOCKED`. The actual
Inventory implementation must still atomically combine the eventual stock
mutation, operation evidence, and result outbox in its system of record. The
gRPC layer does not make an external side effect transactional.

Run the streaming contract test:

```bash
pytest -q examples/kad-inventory/tests/test_grpc_ingress.py
```
