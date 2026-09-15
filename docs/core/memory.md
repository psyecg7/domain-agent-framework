# Memory and LanceDB

`MemoryStore` is optional retrieval for supporting text. It is not state
storage, an event log, or a decision engine.

Use it for a supplier note, an incident report, product description, or an
operator observation. Do not use it for current stock, an order status, payment
outcome, process state, or an idempotency claim. Those are authoritative domain
facts and belong in a `StateStore` or another domain-owned system.

[`memory-lancedb`](../../memory-lancedb/README.md) implements this port with
semantic search. Your application explicitly saves `Memory` records. It does
not copy events, Kafka messages, or database rows automatically.

When a raw `Agent` has both a `MemoryStore` and a context-style reasoner, it
searches memory before reasoning:

```text
event type + current entity ID/type → MemoryStore.search() → ReasoningContext.memories
```

The reasoner receives those matches as optional context and can make a
recommendation. Deterministic policy still decides whether any action is
allowed. `AgentApp` does not configure memory retrieval; use raw `Agent` when
you need this advanced composition.
