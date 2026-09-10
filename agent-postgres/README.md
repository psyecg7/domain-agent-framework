# agent-postgres

PostgreSQL `StateStore` adapter with compare-and-swap optimistic locking. A
state passed to `save` must have a version exactly one greater than its stored
version; concurrent writers receive `ConcurrentStateUpdate`.

`PostgresInventoryReservationAuthority` is a concrete Inventory adapter, not a
generic core primitive. It conditionally updates Inventory stock and writes
operation-keyed reconciliation evidence in the same SQL transaction. A stale
signed precondition yields business `CONFLICT`; a database failure rolls back
both the stock and evidence writes.
