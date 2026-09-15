# Use the framework from another language

`agent-core` and `agent-app` are Python libraries. You do not import them from
Java, Go, TypeScript, or Rust.

Instead, each service implements its own local domain code and communicates at
the service boundary through the versioned artifacts in
[`protocol/`](../protocol/README.md).

```text
Go Inventory service  ── event-envelope v1 ──> Kafka/Redpanda ──> Python Order service
Java Policy service   ── enterprise authorization v1 ──> Executor service
```

This is intentionally narrower than a universal multi-language runtime. The
protocol makes events and high-risk authorization portable; it does not make
business policy, process recovery, or database ownership generic.

## What another service implements

### Produce or consume events

Use [`event-envelope-v1.schema.json`](../protocol/events/event-envelope-v1.schema.json).
Every new producer emits `protocol_version: 1`, an event identity, target
entity identity, timestamp, and any applicable source, lineage, or idempotency
fields. Domain fields such as `available_quantity` remain top-level fields
owned by that event type's domain contract.

Preserve these fields exactly:

- `event_id`: one delivered event identity;
- `metadata.correlation_id`: the request or process lineage shared by related
  messages;
- `metadata.causation_id`: the direct message or operation that produced this
  event; and
- `idempotency_key`: the stable business-operation identity when the domain
  uses one.

Consumers accept legacy versionless records only as a migration aid. They must
reject an explicit version they do not understand. Use the
[event vector](../protocol/test-vectors/events/event-envelope-v1.json) as an
exact encode/decode check in the other language.

### Verify an Enterprise authorization

Use the Enterprise command and authorization schemas together with the
[signing rules](../protocol/enterprise/signing-v1.md). An Executor in another
language must:

1. decode and validate both envelopes;
2. recompute the signed parameter and precondition hashes;
3. verify the Ed25519 signature with the trusted `key_id` public key;
4. reject a wrong audience, invalid time window, revoked decision, or claimed
   replay identity; and
5. let the target domain atomically enforce its own signed preconditions.

The [authorization vector](../protocol/test-vectors/enterprise/authorization-v1.json)
contains a public key, canonical claim bytes, valid signature, altered command,
and verification time. A non-Python Executor is interoperable only after it
passes all of those cases unchanged.

## What does not cross the boundary

Do not serialize a Python `State`, `Policy`, `ActionExecutor`, or in-memory
process object and call that portability. Each service owns its own state,
policy implementation, storage transaction, retry behavior, and reconciliation
logic. The protocol carries explicit facts and outcomes between those owners.

Likewise, `CONFLICT`, `EXISTS`, `ABSENT`, and `STILL_UNKNOWN` are domain result
vocabularies. The framework demonstrates their use but does not impose one
generic workflow on all services.

## Safe rollout

1. Add the protocol vector test to the new service's own CI.
2. Make its event producer emit v1 while existing Python consumers continue to
   accept legacy events.
3. Deploy consumers that understand v1 before requiring v1 from every
   producer.
4. Remove legacy acceptance only in a future documented protocol version.

Protocol versions are independent of Python package versions. See the short
[compatibility policy](../protocol/compatibility.md) before changing a wire
field or signing rule.
