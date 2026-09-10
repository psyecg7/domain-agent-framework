# agent-delta

`agent-delta` is the persistent state adapter for the generic `agent-core` runtime.

It also contains an Inventory-specific reference adapter,
`DeltaReservationEvidenceStore`, for the 2S reconciliation contract. It stores
operation-keyed reservation effect evidence separately from current inventory
state and separately from result-event delivery. This is intentionally domain
code, not a new core primitive.

`DeltaProcessStore` is a separate adapter for an application's own
`ProcessStore`-shaped snapshot contract. It does not define a generic process
model or state machine. On a Delta operational read/write failure it raises
`ProcessStoreUnavailable`; startup or the calling infrastructure must retry or
block processing rather than fabricate an empty process snapshot.

`DeltaComplianceApprovalEvidenceStore` is a Compliance-domain adapter for
append-only human approval evidence. Its `PENDING`/`APPROVED`/`DENIED`/
`CONFLICT` vocabulary belongs to Compliance and Order policy; it is not a
generic core approval primitive. Delta operational failures become the narrow
`ApprovalEvidenceUnavailable` error rather than an approval result.

## Architecture

The dependency direction is intentionally narrow:

```
agent-redpanda -> agent-core <- agent-delta
```

The core keeps the `StateStore` abstraction. Delta is an implementation detail for persistence.

## Responsibilities

`agent-core`:

- generic state semantics
- runtime behavior
- policy and decision lifecycle

`agent-delta`:

- Delta table access
- serialization/deserialization of `State`
- persistent current-state storage

## Current semantics

This adapter provides persistent current state storage for the logical key:

```
(entity_type, entity_id)
```

It does not implement event sourcing, distributed transactions, or exactly-once state transitions.

## Limitations

- concurrent writes are not governed by a custom optimistic locking strategy in this milestone
- historical versions are not treated as the primary query model
- the adapter stores a single current state record per logical entity
- only JSON-serializable state values are preserved in the current implementation
- `DeltaReservationEvidenceStore` is durable across restarts, but its
  idempotent-write and conflict behavior currently applies only to serialized
  writers; it does not yet provide a cross-process compare-and-swap or
  write-serialization guarantee
- an operational Delta read failure is represented internally as
  `EvidenceUnavailable`; `InventoryReservationAuthority` maps that specific
  condition to the 2S result `STILL_UNKNOWN`, rather than leaking it across
  the capability boundary
- `DeltaProcessStore` is durable across restarts but is not safe for concurrent
  writers to the same `process_id` without an external write-serialization
  contract
- `DeltaComplianceApprovalEvidenceStore` is durable across restarts but is not
  safe for concurrent writers to the same `operation_id` without an external
  write-serialization contract

## Example

```python
from agent_delta import DeltaStateStore
from agent_core import State

store = DeltaStateStore("/tmp/agent-state")
state = State(entity_id="sku-42", entity_type="inventory_item", values={"available_stock": 3}, version=1)
store.save(state)
loaded = store.get("sku-42", "inventory_item")
print(loaded)
```

## Core principle

`agent-core` depends on the abstract `StateStore` contract, not on Delta Lake. Delta is only one persistence implementation.
