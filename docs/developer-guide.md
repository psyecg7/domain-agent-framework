# Developer guide

Start with `agent-app`. It provides one local application object without
requiring a database, queue, reasoner, or adapter implementation:

```python
from agent_app import AgentApp
from agent_core import Event

app = AgentApp()

@app.policy("measurement.received")
def investigate(state):
    if state.values.get("temperature", 0) > 40:
        return app.decide("INVESTIGATE", severity="MEDIUM", reason="threshold exceeded")

@app.action("INVESTIGATE")
def notify(action):
    print(f"Would investigate: {action.entity_id}")

app.process(Event("measurement.received", "sensor-1", "sensor", {"temperature": 42}, source="telemetry"))
```

This is the whole required mental model:

```text
Event -> State -> deterministic policy -> Decision -> Action -> action handler
```

Your policy is deterministic domain code. It returns an `app.decide(...)`
declaration; the runtime materializes the Decision, creates an Action only from
that Decision, and then invokes the matching registered action handler. If it
returns `None`, no action executes.

`app.decide(...)` creates a target-independent `DecisionSpec`; it does not
read hidden event context. `AgentApp` applies that declaration to the `State`
already passed into the policy after the policy returns. A policy may return a
single `DecisionSpec`, a raw `Decision`, an iterable of either, or `None`.

## When to add more

Keep the quickstart API for a local tool, prototype, or single-process service.
Use the lower-level `Agent(...)` constructor only when you need one of these:

| Need | Add |
| --- | --- |
| Survive restart | a `StateStore` adapter such as `agent-postgres` or `agent-delta` |
| Publish/consume events | an application transport such as `agent-redpanda` |
| Ask an AI for non-authoritative advice | a `Reasoner`; policy still decides |
| Ask another domain for a fact | capabilities and ordinary result events |
| Protect high-risk cross-service effects | `agent-enterprise` Policy/Executor authorization |
| Safely coordinate several database workers | `agent-postgres` atomic operation/outbox support |
| Call a payment, device, or third-party API | the [external side-effect boundary](external-effect-boundary.md) |

The quickstart API is intentionally local and in-memory. It is not a hidden
production mode and does not change the framework's authority boundary.

## Verify a checkout

From the repository root, run:

```bash
bash scripts/verify_framework.sh
```

This runs deterministic unit and conformance checks, then—when Docker Compose,
`confluent-kafka`, and `psycopg` are available—starts the local
Redpanda/PostgreSQL profile and runs its real integration proofs. It reports a
clear `SKIPPED` result when that optional infrastructure cannot run. Use
`VERIFY_INFRA=0 bash scripts/verify_framework.sh` for the deterministic tier
only, or `KEEP_INFRA_STACK=1` to leave a stack the script started running for
local inspection.

## Partial failure

If one domain effect succeeds and a later one fails, record that fact as a
partial completion; do not silently undo the successful effect. Recovery is a
new, explicit domain decision. For example, Order may request
`inventory.reservation.release`; Inventory then owns the release and returns a
normal result event. The test
`test_recovery_requires_explicit_order_decision_and_remains_event_driven`
demonstrates the full request, Inventory release, and result-event lineage.

If the release request cannot be published, persist it in the Order
application's recovery outbox before attempting publication. If publication
fails before broker acceptance, it remains `PENDING` after restart; once
published, a missing result requires the domain's normal reconciliation path.
The test
`test_failed_release_publish_survives_restart_until_inventory_confirms_recovery`
demonstrates that path, including a duplicate delivery that produces only one
Inventory release effect.

If publication succeeded but the release result was lost, Order must not infer
success. It asks the Inventory-owned
`inventory.reservation.release.reconcile` capability about the stable release
operation ID. Inventory answers from its own durable evidence using
`EXISTS`, `ABSENT`, `CONFLICT`, or `STILL_UNKNOWN`. The
`test_pharmacy_release_reconciliation.py` conformance tests prove the mapping:
only `EXISTS` confirms recovery; every other outcome remains an explicit Order
recovery decision or escalation.

## Multi-worker and external effects

For a PostgreSQL-backed domain with several workers, use
`PostgresAtomicOperationStore` to commit the domain mutation, stable operation
claim, outcome, and outbound outbox record in one transaction. The publisher
still runs after commit, so consumers must tolerate redelivery of the stable
event identity. Run its real PostgreSQL race test before claiming multi-worker
safety in a deployment.

For a payment provider, device, or third-party API, no database transaction can
make the provider call atomic. Use one stable operation ID as the provider's
idempotency key. A lost response after possible acceptance is `UNKNOWN`, not a
failure or success; reconcile through the provider before retrying, recovering,
or escalating. See the [external side-effect boundary](external-effect-boundary.md)
and adopt its `agent-conformance` checks.
