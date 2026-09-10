# Milestone 2S - Domain Reconciliation Contract Stress Test

## Objective

2S tests whether an `UNKNOWN` reservation can be reconciled through the existing `Intent -> Capability -> Event` mechanism. It deliberately does not add a generic reconciliation service, primitive, workflow engine, saga, coordinator, retry manager, or compensation framework.

## Hypothesis

> An unresolved operation can be reconciled by invoking a domain-owned capability through the existing Intent -> Capability -> Event mechanism, without a generic reconciliation framework.

The experiment uses Inventory as the authority for reservation truth and Order as the owner of process meaning.

## Scenarios

| Scenario | Initial state | Request | Expected result | Observed result | Authority | Invariant | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A: unknown to success | Reservation exists, result lost | `inventory.reservation.reconcile` | `EXISTS`, process succeeds | Process became `SUCCEEDED`; no new reservation | Inventory | Domain authority, event boundary | PASS |
| B: unknown to failure | Reservation absent | Reconcile operation | `ABSENT`, process fails | Process became `FAILED` from explicit evidence | Inventory | Missing evidence is not failure | PASS |
| C: still unknown | Inventory cannot establish truth | Reconcile operation | `STILL_UNKNOWN` | Process remained `STILL_UNKNOWN` | Inventory | UNKNOWN is legitimate | PASS |
| D: conflict | Inventory evidence conflicts | Reconcile operation | `CONFLICT` | Process preserved `CONFLICT` | Inventory | Process cannot choose authority | PASS |
| E: restart | Process rebuilt after lost result | Reconcile original operation | Same operation resolves | Original process/operation identity preserved | Order + Inventory | Process state is not domain truth | PASS |
| F: duplicate request | Reservation already exists | Same reconciliation request twice | Repeat evidence, no mutation | Same status returned; mutation count unchanged | Inventory | Reconciliation idempotency differs from reservation idempotency | PASS |
| G: late result | Newer reconciliation observed first | Old result arrives later | Older evidence cannot overwrite newer | Sequence-qualified old result ignored | Order process contract | Stale evidence must not regress state | PASS |
| H: retry | Same operation, new attempt | Reconcile attempt 2 | Operation and attempt remain distinct | Attempt identity preserved | Order + Inventory contract | Operation != attempt | PASS |
| I: human request | Process unresolved | Human-originated Intent | Same capability event path | Same invocation boundary used | Human/application | Intent/capability symmetry | PASS |
| J: no compensation | Reservation exists | Reconcile status only | Observe, do not release/cancel | No release capability or mutation emitted | Order business decision | Reconciliation != compensation | PASS |
| K: no unnecessary mutation | Reservation exists | Read/evidence request | State observation only | Reservation mutation count unchanged | Inventory | Query is not side effect | PASS |
| L: Inventory unavailable | No response | Reconcile request cannot complete | Remains unresolved | Process stayed `UNKNOWN` | Infrastructure/process boundary | PASS |

## Responsibility analysis

| Concern | Owner | Evidence |
| --- | --- | --- |
| Reservation truth | Inventory | Reconciliation returns `EXISTS`, `ABSENT`, `CONFLICT`, or `STILL_UNKNOWN` |
| Reservation idempotency | Inventory | Reconciliation does not mutate reservation state |
| Process UNKNOWN state | Order process | Process remains unresolved without authoritative evidence |
| Reconciliation request | Order/business process | Intent preserves operation, attempt, and correlation |
| Transport delivery | Infrastructure | Invocation and result are ordinary events |
| Event duplicate recognition | Process/domain as appropriate | Repeated reconciliation result is not re-applied |
| Conflict resolution | Inventory | Process preserves conflict rather than selecting a winner |
| Compensation | Explicit business process/domain semantics | No release or cancellation is inferred |
| Human request | Intent/capability boundary | Human causation uses the same invocation event |
| Generic workflow | Nobody | No generic workflow behavior was needed |

## Inventory reconciliation contract

The minimum Inventory contract discovered is domain-specific:

- Input identifies the original `operation_id`, a specific `attempt_id`, and a new `reconciliation_id`.
- The reconciliation request is a new event and request identity; it is not a new reservation operation.
- Inventory returns authoritative status: `EXISTS`, `ABSENT`, `CONFLICT`, or `STILL_UNKNOWN`.
- The request is observational and must not create or release a reservation.
- Inventory owns the evidence and idempotency rules for reservation truth.
- Order changes process status only from the returned evidence.
- Order assigns a monotonic `reconciliation_sequence` when it requests
  evidence; Inventory echoes it unchanged, and Order ignores lower or equal
  sequences. Event arrival order alone is insufficient.

## Durable Inventory evidence reference

The initial contract fixture is now exercised with
`agent_delta.DeltaReservationEvidenceStore`. Inventory stores evidence by
`operation_id` separately from aggregate state and result-event delivery. A
restart can therefore answer `EXISTS` for a reservation whose original result
was not observed by Order; Order remains `UNKNOWN` until it receives and
observes a later ordinary reconciliation result event.

This is a durable reference adapter, not a complete execution ledger: Delta
writes in the current adapter do not yet impose a cross-process
compare-and-swap/write-serialization contract, so same-status idempotency and
conflict detection apply to serialized writes only. A Delta read failure is
translated narrowly into `EvidenceUnavailable`, which Inventory maps to its
authoritative `STILL_UNKNOWN` result; invalid domain inputs and unrelated
programming errors are not swallowed. The vocabulary, capability ID, and Order
interpretation remain the frozen 2S contract.

## Redpanda transport status

`agent-redpanda` now provides an Event transport/dispatcher that serializes the
ordinary reconciliation invocation and result events and commits consumer
offsets only after successful handlers. This establishes an at-least-once
delivery posture for the adapter; duplicate events remain a domain/process
concern handled by the existing event and reconciliation identities.

The broker integration tests are opt-in. `docker-compose.redpanda.yml` provides
a local single-node broker; install `./agent-redpanda` and `./agent-delta`, then run them with
`REDPANDA_BOOTSTRAP_SERVERS=localhost:19092 pytest -q agent-redpanda/tests/test_reconciliation_integration.py`.

### Broker-run result

The three acceptance assertions were executed against the local Redpanda
service: **3 passed**. This validates actual broker redelivery after a handler
failure, newer-before-older reconciliation delivery, and lineage preservation
through consumer restart. The complete broker-enabled Python 3.12 suite then
passed with **226 passed, 0 skipped**. It does not validate broker
unavailability. A two-consumer ownership-handoff assertion covers one keyed
Delta evidence write across a consumer-group handoff and passed against the
local broker (**4 passed** in the dedicated integration suite).

### Broker recovery

The local Compose configuration now uses the named `redpanda-data` volume.
`scripts/redpanda_recovery_check.py` is an explicitly enabled chaos check: it
fails an Order handler before offset acknowledgement, restarts the local
broker, and requires unchanged redelivery plus Order deduplication. It is not
part of ordinary pytest because it intentionally interrupts the broker.

`RedpandaProducer` now treats an undelivered flush result as an infrastructure
failure rather than returning success. The manual recovery check passed against
the local broker: the uncommitted event survived the restart, redelivered, and
Order applied it once. Transient `librdkafka` disconnect logs during the
intentional restart are expected infrastructure observations, not domain
outcomes.

### Required broker-run assertions

The broker integration is not complete merely because it does not crash. Its
pass/fail criteria are:

1. A handler failure leaves the offset uncommitted, the broker redelivers the
   same event, and reconciliation/event identity prevents a second process
   application.
2. A newer reconciliation sequence delivered before an older one preserves the
   newer outcome and ignores the older evidence.
3. `correlation_id` and `causation_id` survive publish, consume, consumer
   restart or rebalance, and redelivery unchanged.
4. Two members of one consumer group hand off a same-key operation without
   concurrent Delta evidence writes; the first write records the effect and
   the handoff delivery is idempotent.

Until these assertions run against a real broker in normal validation, the
skipped integration test is verification debt, not a neutral test exclusion.

## Durable Order process snapshot reference

`agent_delta.DeltaProcessStore` now persists the frozen Order
`PurchaseProcess` snapshot shape—identity, facts, operations, applied event
IDs, status, and recovery reason—without adding a generic core process model.
The existing restart/replay and partial-completion assertions run with the
Delta store substituted for the in-memory `ProcessStore`.

If Delta cannot reconstruct a process snapshot, `DeltaProcessStore` raises the
narrow `ProcessStoreUnavailable` error. The deliberate handling is to block
process startup/handling for infrastructure retry; it must never substitute a
fresh or empty process. This is not a domain `STILL_UNKNOWN` result because
Order cannot safely reason without its own process state.

Like reservation evidence, the current Delta reference store has no
cross-process compare-and-swap or write-serialization guarantee for concurrent
writes to the same `process_id`.

## Inventory vs PIM comparison

PIM publication reconciliation has the same structural shape: an unresolved operation, a domain-owned capability, an evidence result, and a process update. The business meanings differ:

```text
Inventory: determine whether reservation RES-123 exists.
PIM:       determine whether publication PUB-7 reached its target.
```

Inventory reconciliation concerns stock reservation truth and may return `EXISTS` or `ABSENT`. PIM reconciliation concerns publication target state and may return `PUBLISHED`, `NOT_PUBLISHED`, or an unresolved target condition. The shared shape is not sufficient evidence for a generic `Reconciliation` primitive.

## Release recovery reconciliation addendum

The same authority rule now has a release-side conformance example. If Order
persisted and published a reservation-release request but lost Inventory's
normal result, Order does not infer release success. It asks the
Inventory-owned `inventory.reservation.release.reconcile` capability about the
stable release operation ID. Inventory answers from its own durable evidence:

```text
EXISTS         -> Inventory applied the release; Order confirms recovery.
ABSENT         -> no release evidence; Order needs an explicit retry decision.
CONFLICT       -> evidence is contradictory; Order escalates recovery.
STILL_UNKNOWN  -> Inventory cannot answer; Order remains unresolved.
```

The conformance test exercises a lost result across an Order restart and all
four outcomes through the actual capability-invocation/event shape. This is a
demonstrated, gateable domain pattern—not a generic framework reconciliation
or compensation feature.

## Verified

- `UNKNOWN` can move to success, failure, conflict, or remain unknown through a domain-owned capability.
- Reconciliation travels through the same Intent -> Capability -> Event mechanism.
- Inventory remains authoritative for reservation truth.
- Reconciliation does not create a new reservation or automatically compensate.
- Inventory-authoritative evidence can also resolve a lost reservation-release
  result without Order inventing success.
- Duplicate reconciliation requests are safe in the test without a generic idempotency manager.
- Operation, attempt, reconciliation, event, correlation, and causation identities remain distinct.
- Restart does not create a new reservation operation.
- Human and agent requests use the same capability/event boundary.
- No direct agent calls, shared domain state, automatic capability chaining, or core changes were introduced.

## Inference

- Reconciliation is naturally an Inventory business capability, not a generic framework service.
- The process owns the interpretation of authoritative evidence, while the domain owns the truth being queried.
- The commonality between Inventory and PIM is structural and process-shaped, but their result vocabularies and authority contracts are domain-specific.
- Reconciliation and compensation are separate responsibilities: one establishes truth, the other changes business state.

## Speculation

- Domain adapters may eventually need a standard way to express operation attempts and freshness, but the correct ownership and schema are not established.
- A future application utility could reduce repeated event-correlation code, but it must not become a generic reconciliation engine.
- Durable domain evidence and external-system reconciliation may require separate contracts.

## What 2S disproved

2S disproved that `UNKNOWN` requires a second generic recovery architecture, that reconciliation should mutate business state, and that the process can infer reservation truth from missing events. It also disproved that structurally similar Inventory and PIM reconciliation necessarily justify a shared primitive.

## Open questions

- Whether reconciliation should target the whole operation or a specific attempt in production.
- How Inventory persists and exposes authoritative evidence after restart.
- How stale reconciliation evidence is versioned durably.
- How external legacy systems produce authoritative or still-unknown answers.
- What human recovery procedure applies to unresolved conflicts.
- Whether result publication guarantees need a domain-specific delivery contract.

## Assessment

**PARTIAL**

The basic mechanism works and preserves all major ownership boundaries. The remaining operation-versus-attempt freshness contract and durable domain-evidence questions are genuine unresolved semantics, but they do not justify a generic reconciliation primitive or workflow framework.
