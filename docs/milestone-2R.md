# Milestone 2R - Architecture Invariants Stress Test

## Objective

2R stress-tests the pharmacy architecture from 2A-2Q under duplicated events, retries, lost results, restart, capability loss, partitions, stale evidence, conflicting results, and human intervention. It does not add a workflow engine, reconciliation service, idempotency manager, or `agent-core` primitive.

## Existing architecture

The tested path remains:

```text
Intent -> Capability -> Invocation Event -> Domain Agent
-> Policy -> Decision -> Action -> Side Effect
-> Result Event -> Business Process State
```

The domain agent owns authoritative business state, policy, decisions, actions, side effects, and domain idempotency. The business process owns process identity, operation identity, observed outcomes, progression, unresolved status, and explicit recovery decisions. `CapabilityInvoker` discovers one capability and publishes one invocation event. Infrastructure transports, stores, restarts, and redelivers evidence but does not infer business truth.

## Invariants

- **I1 - Domain authority:** the domain owning a fact is authoritative for it.
- **I2 - Process authority:** the process owns what was requested, observed, unresolved, and explicitly decided next.
- **I3 - Event evidence:** an event is transported evidence, not automatic proof of a side effect.
- **I4 - Operation identity:** `operation_id` identifies the intended business operation.
- **I5 - Event identity:** `event_id` identifies one event occurrence; multiple events may concern one operation.
- **I6 - Process durability != operation durability:** a process snapshot records process knowledge, not domain execution truth.
- **I7 - Event durability != side-effect durability:** a durable event is not the domain source of truth.
- **I8 - Missing evidence != failure:** absent results can remain `UNKNOWN`.
- **I9 - Duplicate delivery != duplicate execution:** business idempotency belongs to the owning domain.
- **I10 - CapabilityInvoker does not execute:** it publishes a request only.
- **I11 - Recovery is explicit:** timeout, missing evidence, duplication, or partial completion does not automatically trigger recovery.
- **I12 - UNKNOWN is legitimate:** insufficient evidence is a valid state.
- **I13 - Domain invariants cannot be delegated to infrastructure:** reservation safety is an Inventory invariant.
- **I14 - Business process semantics must not become generic workflow mechanics:** recurring fields do not justify generic process APIs.

## Adversarial scenarios

| Scenario | Expected semantic result | Observed result | Invariant | Responsibility | Result |
| --- | --- | --- | --- | --- | --- |
| Duplicate invocation, same reservation operation | One business reservation | Inventory effect authority applies once | I4, I9, I13 | Inventory domain | PASS |
| Same operation, different event IDs | Distinct events, same operation | IDs remain distinct and operation ID matches | I4, I5 | Domain operation contract | PASS |
| Duplicate result event | No second process advancement | Event ledger recognizes one result ID | I3, I5, I9 | Process | PASS |
| Side effect succeeds, result is lost | Process remains `UNKNOWN` | Inventory retains effect; process does not infer success | I1, I3, I8 | Domain + process | PASS |
| Process restart after side effect | No replay or invented success | Restarted process remains `UNKNOWN` | I6, I12 | Process | PASS |
| Retry after `UNKNOWN` | Same operation, distinct attempt | Attempt increments while operation ID remains stable | I4, I8 | Order process + Inventory contract | PASS |
| Late old-attempt result | No completion of current attempt | Result is tracked stale; current operation remains requested | I4, I8 | Process | PARTIAL |
| Conflicting success/failure evidence | No arbitrary winner | Status becomes `CONFLICT`; evidence retained | I3, I8 | Owning domain reconciliation | PASS |
| Process state lost, domain state survives | Process cannot claim completion | Inventory effect remains authoritative | I1, I6 | Inventory domain | PASS |
| Inventory unavailable | Observation remains unresolved | Capability availability returns no result; process remains `UNKNOWN` | I8, I10 | Infrastructure/process boundary | PASS |
| Capability disappears mid-process | No inferred business failure | Process has no successful observation | I8, I10 | Infrastructure/process boundary | PASS |
| Network partition | Neither success nor failure inferred | Request evidence exists without result | I3, I8 | Infrastructure | PASS |
| Human intervention | Same capability/event boundary | Reconciliation request is an ordinary invocation event | I10, I11 | Order application | PASS |
| Multiple independent operations | Identities remain separate | PIM, reservation, and price operation IDs do not collapse | I4, I5, I6 | Business process | PASS |

## Failure classification

| Problem | Category | Why | Current owner | New primitive justified? |
| --- | --- | --- | --- | --- |
| Generic invocation does not deduplicate same operation | DOMAIN_RESPONSIBILITY | Only Inventory knows whether duplicate reservation is the same business effect | Inventory | No |
| Stale result status remains `REQUESTED` rather than a richer current-attempt state | PROCESS_RESPONSIBILITY | The process tracks stale evidence but lacks a complete attempt-status contract | Order process | No generic primitive yet |
| Process cannot prove an effect after lost result | INFRASTRUCTURE_RESPONSIBILITY | Effect evidence and result delivery are separate durability guarantees | Inventory persistence/transport | Not by this experiment |
| Conflicting results need authoritative resolution | DOMAIN_RESPONSIBILITY | Transport cannot decide which business evidence is true | Inventory | No |
| Capability/network unavailability has no business result | INFRASTRUCTURE_RESPONSIBILITY | Technical inability to observe is not business rejection | Transport/application | No |

No `ARCHITECTURAL_VIOLATION` or `MISSING_PRIMITIVE` was demonstrated. A missing convenience API is not treated as a missing semantic primitive.

## Verified

- Domain authority remains distinct from process observation.
- Event, correlation, process, operation, attempt, action, and causation identities are distinct.
- Missing results remain `UNKNOWN` rather than becoming failure or success.
- Process restart does not prove or repeat a missing side effect.
- Same-operation duplicate safety is an Inventory business invariant, not generic event deduplication.
- `CapabilityInvoker` remains one-hop and does not execute, retry, compensate, or infer results.
- Human reconciliation can use the same Intent -> Capability -> Event boundary.
- No shared domain state, direct agent call, workflow engine, saga, or new core primitive was added.

## Inference

- Process durability, operation durability, event durability, and side-effect durability are independent guarantees.
- Retry and reconciliation require cooperation from the operation-owning domain.
- The architecture survives as an event-driven semantic model, but it cannot claim generic exactly-once execution.

## Speculation

- A future application-level reconciliation contract may be useful, but its ownership and API are not justified by this experiment.
- Domain adapters may eventually need explicit attempt and operation fields in their action/result schemas.
- Durable effect evidence may require an outbox-like or domain-specific mechanism, but no infrastructure choice follows from these tests alone.

## What 2R disproved

2R disproved that event deduplication guarantees business idempotency, that a process snapshot proves a side effect, that a missing result means failure, and that a retry can be made safe without a domain operation contract. It also disproved that capability invocation alone resolves uncertainty after partitions or crashes.

## Open questions

- How Inventory exposes authoritative reconciliation after a domain restart.
- How stale and conflicting attempts are resolved in production.
- Which operation and attempt fields belong in domain-specific action/result schemas.
- How durable result publication and observation acknowledgements should work.
- What human recovery procedure is appropriate for unresolved reservations.

## Assessment

**PARTIAL**

The architecture preserves its core invariants under the tested adversarial scenarios, but it exposes genuine unresolved execution boundaries around operation idempotency, effect evidence, reconciliation, and stale/conflicting results. Those are not reasons to add a generic workflow or execution engine yet; they require explicit domain and operational contracts first.
