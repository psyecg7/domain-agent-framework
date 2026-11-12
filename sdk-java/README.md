# sdk-java

`sdk-java` is the Java implementation of reusable framework mechanics. It is
not a Java rewrite of `agent-core`: each Java service still owns its domain
policy, database tables, recovery choices, and business outcomes.

It provides:

- event-envelope v1 JSON encoding and decoding;
- Kafka publishing and dispatch that commits offsets only after every handler
  succeeds;
- PostgreSQL transaction helpers for one operation claim, domain mutation, and
  outbox row in one transaction, plus multi-worker outbox leasing/publishing; and
- a Protobuf/gRPC bidirectional command-ingress adapter, including a
  PostgreSQL Inventory inbox with durable acceptance and worker leasing; and
- Enterprise execution-command verification with Ed25519, binding hashes,
  audience/time checks, and a caller-supplied atomic replay store.

The shared contract is in [`../protocol`](../protocol/README.md). This SDK
must pass its fixed event and authorization vectors before it is used with a
service in another language.

## Build and test

Java 17+ and Maven 3.9+ are required:

```bash
cd sdk-java
mvn test
```

The tests intentionally read the shared `../protocol/test-vectors` files; they
do not copy them. A changed vector therefore breaks every SDK until the
protocol version is deliberately updated.

## gRPC command ingress

The generated Java types come from the shared
[`inventory-command-v1.proto`](../protocol/kad/inventory-command-v1.proto),
not from a Java-only copy. `GrpcInventoryCommandClient` sends many commands on
one bidirectional stream; `GrpcInventoryCommandGateway` returns `ACCEPTED`
only after the configured `InventoryCommandInbox` persists them.

```java
PostgresInventoryCommandInbox inbox = new PostgresInventoryCommandInbox(dataSource, "inventory_command_inbox");
inbox.migrate(); // controlled deployment migration

Server server = ServerBuilder.forPort(8443)
    .addService(new GrpcInventoryCommandGateway(inbox))
    .build()
    .start();
```

An acceptance receipt is not a reservation result. Inventory workers claim
commands with `inbox.claimPending("inventory-worker-1", 100, 30)`, execute the
domain-owned atomic mutation/evidence/outbox transaction, then call
`markCompleted`. A reconnect safely resubmits the same `operation_id` and
`attempt_id`; the inbox returns `DUPLICATE`. Production servers must use TLS
and service authentication; the in-process test is intentionally transport-free.

## Minimal Kafka consumer

```java
KafkaEventDispatcher dispatcher = new KafkaEventDispatcher(consumer, new EventEnvelopeCodec());
dispatcher.subscribe("inventory.stock_changed", event -> inventory.apply(event));
dispatcher.dispatch(Duration.ofSeconds(1));
```

If `inventory.apply` throws, `dispatch` does not commit the polled offsets.
Kafka may redeliver; the domain must use its stable event or operation identity
to make duplicate delivery safe.

## What this SDK does not decide

It does not expose a generic process engine, retry policy, compensation rule,
or business state model. `PostgresAtomicOperationStore` supplies a transaction
boundary; the callback supplied by the domain performs only that domain's SQL
mutation. It must never call a payment provider, device, or broker inside the
transaction.

`PostgresOutboxPublisher` leases a disjoint batch with PostgreSQL row locks,
publishes only after the original transaction committed, and marks a row as
published only after Kafka accepts it. This is at-least-once delivery: a crash
after broker acceptance can publish again, so consumers still deduplicate by
stable event or operation identity.

```java
PostgresAtomicOperationStore store = new PostgresAtomicOperationStore(dataSource, "orders", codec);
store.migrate(); // deployment migration job, not every replica startup

store.executeOnce("order:ORD-42:create", connection -> {
  // Execute Order-owned SQL using this same JDBC connection.
  return "SUCCEEDED";
}, orderCreatedEvent);

new PostgresOutboxPublisher(store, kafkaPublisher, event -> "order.events")
    .publishPending("order-publisher-1", 100, 30);
```

Use `PostgresReplayStore` with `EnterpriseAuthorizationVerifier` when several
Executor replicas share replay protection. Call its `migrate()` method from the
same controlled deployment migration job.
