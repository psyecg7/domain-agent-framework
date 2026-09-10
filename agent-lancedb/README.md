# agent-lancedb

`agent-lancedb` is the optional semantic-memory adapter for the generic `agent-core` runtime.

## Architecture

```text
Redpanda -> agent-redpanda -> agent-core
                              /        \
                       StateStore   MemoryStore
                           |             |
                     agent-delta   agent-lancedb
                           |             |
                         Delta        LanceDB
```

The responsibilities are deliberately separate:

- Redpanda carries events and transport messages.
- Delta persists authoritative structured current state through `StateStore`.
- LanceDB stores optional semantic context through `MemoryStore`.
- `agent-core` defines technology-neutral `Memory`, `MemoryResult`, and `MemoryStore` types.

Semantic memory is supplemental context. It is not authoritative state and it is not event history. The adapter does not automatically copy events into LanceDB, and `Agent.process()` does not require or query memory.

## Usage

```python
from agent_core import Memory
from agent_lancedb import LanceDBMemoryStore

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

## Embeddings

The default `HashEmbeddingProvider` is deterministic and local. It is suitable for tests and development, but it is not a production-quality language model. A different implementation can satisfy the `EmbeddingProvider` protocol without adding an embedding dependency to `agent-core`.

## Persistence and duplicate semantics

The LanceDB database and table persist across `LanceDBMemoryStore` instances. `memory_id` is the stable logical identity. Calling `store()` for an existing ID deletes the current row and adds the replacement, so the current table contains one row for that ID rather than silently accumulating duplicates.

Metadata filtering supports generic `entity_id` and `entity_type` values. The adapter does not encode inventory, product, order, or other domain concepts.

This milestone does not provide distributed transactions, optimistic concurrency, exactly-once transitions, or historical memory versions. Concurrent calls that replace the same `memory_id` have last-writer-wins behavior at the adapter level. LanceDB is used for current semantic-memory records, not as an event store.
