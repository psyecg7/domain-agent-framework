# memory-lancedb

`memory-lancedb` is the optional semantic-memory adapter for the generic `agent-core` runtime.
Read the [core memory reference](../docs/core/memory.md) first for the concise
runtime contract and when semantic memory is appropriate.

## Architecture

```text
Redpanda -> transport-redpanda -> agent-core
                              /        \
                       StateStore   MemoryStore
                           |             |
                     storage-delta   memory-lancedb
                           |             |
                         Delta        LanceDB
```

The responsibilities are deliberately separate:

- Redpanda carries events and transport messages.
- Delta persists authoritative structured current state through `StateStore`.
- LanceDB stores optional semantic context through `MemoryStore`.
- `agent-core` defines technology-neutral `Memory`, `MemoryResult`, and `MemoryStore` types.

Semantic memory is supplemental context. It is not authoritative state and it is not event history. The adapter does not automatically copy events into LanceDB, and `Agent.process()` does not require or query memory.

## When to use it

Use LanceDB when an AI reasoner would benefit from finding related, unstructured
material: a supplier note, an incident summary, a product description, or a
previous operator observation.

Do **not** use it for the facts that decide a business outcome: available
stock, an order status, a payment result, a process status, or an idempotency
claim. Those facts belong in the domain's authoritative state store, usually
PostgreSQL in a multi-worker service.

For example, an Order policy can read `available_quantity = 2` from PostgreSQL
and use LanceDB to retrieve a note saying that the supplier is delayed because
of weather. The note may help a reasoner recommend an investigation. It must
not replace the stock value, and the deterministic policy still decides whether
an action is allowed.

## Usage

```python
from agent_core import Memory
from memory_lancedb import LanceDBMemoryStore

store = LanceDBMemoryStore("/tmp/agent-memory")
store.store(
    Memory(
        memory_id="supplier-delay-1",
        entity_id="product-42",
        entity_type="product",
        content="Supplier delivery was delayed by weather.",
        metadata={"category": "supply"},
    )
)

results = store.search("supplier delivery delay", entity_type="product", limit=5)
```

Application code chooses what to store. The adapter does not ingest Kafka
messages, events, or database rows automatically.

## How it reaches a reasoner

LanceDB is an advanced `Agent` composition option, not part of the local
`AgentApp` quickstart. Pass a `MemoryStore` when constructing a raw `Agent` and
use a context-style reasoner (`reason(context)`). Before that reasoner runs,
`Agent.process()` searches memory using the current event type and filters the
results to the current entity ID and entity type. It supplies the matches as
`ReasoningContext.memories`.

```text
incoming event
  → authoritative StateStore supplies current facts
  → LanceDB supplies optional related text
  → reasoner proposes advice
  → deterministic policy accepts or rejects that advice
```

The result is deliberately advisory. If you need a different query, broader
scope, or domain-specific retrieval rules, compose that explicitly in your
application rather than treating LanceDB as a hidden source of truth.

## Embeddings

The default `HashEmbeddingProvider` is deterministic and local. It is suitable for tests and development, but it is not a production-quality language model. A different implementation can satisfy the `EmbeddingProvider` protocol without adding an embedding dependency to `agent-core`.

## Persistence and duplicate semantics

The LanceDB database and table persist across `LanceDBMemoryStore` instances. `memory_id` is the stable logical identity. Calling `store()` for an existing ID deletes the current row and adds the replacement, so the current table contains one row for that ID rather than silently accumulating duplicates.

Metadata filtering supports generic `entity_id` and `entity_type` values. The adapter does not encode inventory, product, order, or other domain concepts.

This milestone does not provide distributed transactions, optimistic concurrency, exactly-once transitions, or historical memory versions. Concurrent calls that replace the same `memory_id` have last-writer-wins behavior at the adapter level. LanceDB is used for current semantic-memory records, not as an event store.
