# agent-postgres

PostgreSQL `StateStore` adapter with compare-and-swap optimistic locking. A
state passed to `save` must have a version exactly one greater than its stored
version; concurrent writers receive `ConcurrentStateUpdate`. Database access
or commit failures raise `StateStoreUnavailable` instead, so infrastructure
recovery is never confused with a business/concurrency conflict.

## Schema migrations

Do not rely on application startup to upgrade a deployed database. Run the
versioned migration command before rolling out code that needs a new adapter
schema:

```bash
python -m agent_postgres.migrate \
  --database-url 'postgresql+psycopg://agent:agent@db:5432/agent'
```

It records applied versions in `agent_schema_migrations`, provisions the
current state/operation/outbox/receipt tables, and upgrades the legacy outbox
to the HA lease columns. The command is idempotent. Use `--table-prefix`,
`--state-table`, and `--receipt-table` when a deployment uses non-default
names. Run it as one deployment-controlled migration job, not from every
application replica at startup.

See the [PostgreSQL migration runbook](../docs/postgres-migration-runbook.md)
for backup, verification, canary rollout, and rollback guidance.

## Atomic operation and outbox support

`PostgresAtomicOperationStore` is the optional multi-worker durability path.
It claims a domain-owned `operation_id`, runs a domain-supplied SQL mutation,
records that operation's outcome, and persists an optional outbound `Event` in
one PostgreSQL transaction. A duplicate committed operation returns its stored
outcome without running the mutation again. A mutation failure rolls back the
claim, mutation, and outbox record together.

```python
from agent_core import Event
from agent_postgres import PostgresAtomicOperationStore

atomic = PostgresAtomicOperationStore(database_url)

result = atomic.execute_once(
    "reserve:ORD-42",
    apply=lambda connection: reserve_in_inventory(connection),
    outbox_event=Event(
        "inventory.reserved", "ORD-42", "order",
        {"operation_id": "reserve:ORD-42"}, source="inventory",
        idempotency_key="reserve:ORD-42",
    ),
)
for event in atomic.pending_outbox():
    broker.publish(event)
    atomic.mark_published(event.event_id)
```

`pending_outbox()` is deliberately for one serialized publisher. For multiple
publisher workers, claim an expiring lease before publishing and confirm it
with the same worker identity:

```python
for event in atomic.claim_outbox("publisher-a", lease_seconds=30):
    try:
        broker.publish(event)
        atomic.mark_published(event.event_id, worker_id="publisher-a")
    except BrokerUnavailable:
        atomic.release_outbox_lease(event.event_id, worker_id="publisher-a")
        raise
```

The lease is a work-distribution mechanism, not exactly-once publication: a
worker can crash after broker acceptance and before confirmation. Consumers
must still deduplicate on the stable event or operation identity. Retain or
archive confirmed rows with a deployment-owned retention job; deleting them in
the publish hot path would discard operational evidence.

The callback must make only writes against the supplied database connection.
It must not call a broker, payment provider, device, or other external system:
those systems cannot participate in the SQL transaction. The publisher marks
an event only after broker acceptance; a crash between acceptance and marking
can redeliver the same stable event, so consumers still require idempotency.

This adapter is the production candidate for a PostgreSQL deployment. SQLite
is used in repository tests only to validate transaction shape; it is not the
multi-worker production guarantee. Run the atomicity conformance checks against
the actual PostgreSQL service and its configured isolation level before making
that claim in a deployment.

Run the cross-worker proof with the repository's local Compose stack:

```bash
docker compose -f docker-compose.redpanda.yml up -d postgres
python -m pip install 'psycopg[binary]'
POSTGRES_ATOMIC_DATABASE_URL='postgresql+psycopg://agent:agent@localhost:5432/agent_atomic' \
  pytest -q tests/test_postgres_atomicity_integration.py

# Also proves: database commit -> crash before Redpanda offset commit ->
# redelivery -> duplicate receipt skips the effect.
POSTGRES_ATOMIC_DATABASE_URL='postgresql+psycopg://agent:agent@localhost:5432/agent_atomic' \
REDPANDA_BOOTSTRAP_SERVERS=localhost:19092 \
  pytest -q tests/test_postgres_redpanda_receipt_integration.py
```

The same Compose file also starts Redpanda when invoked without the `postgres`
service name. It stores PostgreSQL data in the local `postgres-data` volume;
remove that volume only when you intentionally want a fresh development
database.

`PostgresInventoryReservationAuthority` is a concrete Inventory adapter, not a
generic core primitive. It conditionally updates Inventory stock and writes
operation-keyed reconciliation evidence in the same SQL transaction. A stale
signed precondition yields business `CONFLICT`; a database failure rolls back
both the stock and evidence writes.

## Broker-event receipt support

`PostgresEventReceiptStore` is the equivalent boundary for an inbound broker
event and a co-located database mutation. It claims the stable `event_id` and
runs the supplied mutation through the same transaction. Commit the broker
offset only after `apply_once` returns; a duplicate receipt means the handler
was skipped because another worker already committed the effect.

```python
from agent_postgres import PostgresEventReceiptStore

receipts = PostgresEventReceiptStore(database_url)

def handle_reserved(event) -> None:
    result = receipts.apply_once(
        event.event_id,
        apply=lambda connection: update_order_from_reservation(connection, event),
    )
    # Both applied and duplicate are safe to acknowledge to the broker.
    # If apply_once raises, leave the offset uncommitted for redelivery.
```

The mutation must use only the supplied connection. This pattern cannot by
itself make external APIs, devices, or a raw `Agent.process()` call exactly
once; use the external-effect and domain reconciliation contracts for those
boundaries. Repository CI runs PostgreSQL atomicity and receipt proofs; run the
same tests against the deployment database before making that production claim.

For the narrower case of a raw Agent that only persists PostgreSQL state,
`PostgresAgentReceiptRunner` composes the receipt and the Agent observation
into one transaction:

```python
from agent_core import Agent
from agent_postgres import (
    PostgresAgentReceiptRunner,
    PostgresEventReceiptStore,
    PostgresStateStore,
)

state_store = PostgresStateStore(database_url)
agent = Agent(state_store, policy_engine)  # no action_executor here
runner = PostgresAgentReceiptRunner(
    agent,
    state_store=state_store,
    receipt_store=PostgresEventReceiptStore(database_url),
)

result = runner.process_once(event)
# Commit the broker offset only after this returns. A duplicate has
# result.receipt.applied == False and result.result is None.
```

The runner rejects Agents with an action executor. It gives exactly-once state
observation only—not exactly-once action execution. Publish or execute actions
through a domain-owned transactional outbox or Enterprise execution boundary.
