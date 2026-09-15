# State and storage

`State` is the current, structured picture of one entity:

```python
State(entity_id="SKU-42", entity_type="product")
```

Each observation updates a named value, increments `version`, and records an
update time. The core makes no claim that an in-memory `State` is durable or
safe for several workers.

`StateStore` is the storage boundary:

```python
store.get(entity_id, entity_type) -> State | None
store.save(state) -> None
```

Choose a store based on the property the domain needs:

| Need | Use |
| --- | --- |
| Local prototype | `agent-app`'s in-memory store |
| Restart-safe shared state and conditional writes | `storage-postgres` |
| Durable evidence/reference storage | `storage-delta` |

Business truth belongs here: stock, order status, payment result, process
status, and any invariant that decides whether an operation is allowed. Do not
put those facts in a reasoner prompt or semantic memory.

For several workers changing the same entity, the domain needs an atomic
conditional write. `storage-postgres` demonstrates operations, mutations, and an
outbox record committed in one SQL transaction. That is an adapter and domain
design concern, not a generic core guarantee.
