# Component map

Start with this page when you are deciding what to add to an application.
Each package has one responsibility. Packages do not silently enable the
responsibilities of other packages.

The optional package prefixes are deliberate: `storage-` owns durable data,
`transport-` moves events, `memory-` retrieves supporting text, and `model-`
produces advisory AI output. They are not interchangeable kinds of integration.

If you first need to understand the core concepts themselves, start with the
[core reference](core/README.md). This page is for choosing packages and
infrastructure after that.

## Start with two packages

| Component | Use it for | It does not provide |
| --- | --- | --- |
| [`agent-core`](core/lifecycle.md) | The agent lifecycle and the rule that only a deterministic Decision can create an Action. | Databases, Kafka, HTTP, LLMs, retries, business rules, or multi-service security. |
| [`agent-app`](../agent-app/README.md) | The normal Python developer API: `AgentApp`, policies, action handlers, and local processing. | Durable storage, a message broker, or production recovery by itself. |

For a small local application, these are enough:

```text
Event → State → policy → Decision → Action → handler
```

Your policy contains the business rule. For example, “temperature above 40
requires investigation” belongs in your application. The core only ensures
that the resulting action came through that policy path.

## Add one capability at a time

| Need | Add | What it owns |
| --- | --- | --- |
| State must survive a restart | [`storage-postgres`](../storage-postgres/README.md) or [`storage-delta`](../storage-delta/README.md) | Storage. PostgreSQL is the choice for shared transactional state and conditional writes. Delta is a durable evidence/reference store. |
| Agents exchange events through Kafka or Redpanda | [`transport-redpanda`](../transport-redpanda/README.md) | Event serialization, broker publishing/consumption, and offset commit after successful handling. |
| An AI model suggests a next step | [`model-openai`](../model-openai/README.md) or [`model-ollama`](../model-ollama/README.md) | Model integration that produces advice. It cannot authorize an Action. |
| Retrieve non-authoritative semantic context | [`memory-lancedb`](../memory-lancedb/README.md) | Memory retrieval. It does not replace domain state or policy. |
| Emit an Event later | [`scheduler`](../scheduler/README.md) | Local delayed Event emission. It does not make the eventual effect safe by itself. |
| Several workers change the same database state | [`storage-postgres`](../storage-postgres/README.md#atomic-operation-and-outbox-support) | Atomic operation claims, domain mutation, and an outbox record in one SQL transaction. |
| Separate Policy and Executor services must distrust each other | [`enterprise`](../enterprise/README.md) | Signed authorizations, replay/revocation stores, OIDC, mTLS, and enterprise reference infrastructure. |
| Verify domain safety patterns in CI | [`conformance`](../conformance/README.md) | Reusable assertions for idempotency, lineage, reconciliation, outboxes, and unresolved external effects. It is test-only. |

## LanceDB: optional context, never business truth

Use `memory-lancedb` only when a reasoner needs supporting text that is useful
but not decisive. A supplier note, an incident report, or a product description
are good candidates. Current stock, order status, payment outcome, and process
status are not: keep those in the domain's authoritative state store.

In the advanced `Agent` API, a context-style reasoner can receive both inputs:

```text
PostgreSQL state: product SKU-42 has available_quantity = 2
LanceDB memory:   supplier delay was caused by weather
                         ↓
reasoner:          recommends investigating the supplier delay
policy:            decides whether that recommendation may create an Action
```

The runtime searches LanceDB only when you explicitly configure a raw `Agent`
with a `MemoryStore` and use a reasoner that accepts `ReasoningContext`. It
searches with the incoming event type and limits results to that event's entity
ID and entity type. `AgentApp` intentionally does not add memory retrieval:
its local quickstart stays deterministic and in-memory.

Your application must explicitly store `Memory` records. No event, Kafka
message, or database row is copied into LanceDB automatically.

## A typical production order flow

```text
agent-app
  receives an Order event and runs deterministic policy

storage-postgres
  saves Order state and a pending outbox record atomically

transport-redpanda
  publishes the outbox record to Kafka/Redpanda

Inventory domain
  performs its own atomic stock update and publishes a result

Order domain
  correlates the result with its saved process state
```

`agent-core` is present in this flow, but it does not own any database row,
Kafka topic, stock rule, or recovery decision. It carries the lifecycle and
authority rule used by the application.

## What remains domain code

You must write and own these parts for each domain:

- business policy, such as stock allocation or fraud limits;
- database tables and domain invariants;
- operation and idempotency identity;
- recovery after partial failure;
- reconciliation with a payment provider, device, or third-party API;
- Kafka topic ownership, partition keys, and message contracts.

The [KAD Inventory example](../examples/kad-inventory/README.md) shows these
choices for one Inventory and Order flow. It is a reference application, not a
feature that becomes available automatically after installing the framework.

## Choosing the smallest setup

| Situation | Install/use |
| --- | --- |
| Local prototype with deterministic rules | `agent-core` + `agent-app` |
| One Python service that must survive restart | Add `storage-postgres` |
| Multiple services communicate asynchronously | Add `transport-redpanda` and a domain-owned process/outbox design |
| AI can advise but must not decide | Add an OpenAI or Ollama reasoner; keep deterministic policy in the application |
| High-risk action across trust boundaries | Add `enterprise` and deployment controls |
