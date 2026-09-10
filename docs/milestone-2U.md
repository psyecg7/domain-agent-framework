# Milestone 2U - Continuous Autonomous Domain Agent

## Objective

2U tests whether one Inventory domain can continuously observe events, maintain authoritative state, reason about changing conditions, produce recommendations, validate them deterministically, execute authorized actions, and react to resulting events without a generic agent loop, planner, workflow engine, or new core primitive.

## Hypothesis

> An autonomous domain can continuously observe events, maintain state, reason about changing conditions, validate recommendations deterministically, execute authorized actions, and react to resulting events using the existing architecture.

## Scenario results

| Scenario | Expected | Observed | Authority | Invariant | Result |
| --- | --- | --- | --- | --- | --- |
| Above threshold | Observation/state update, no action | Stock 12 produced no recommendation/action | Inventory | State is domain-owned | PASS |
| Low stock | Recommendation from current state | Stock 3 produced `REPLENISH` recommendation | Inventory reasoner | Recommendation is non-authoritative | PASS |
| Policy accepts | Decision then Action then event | Approved recommendation emitted `replenishment.requested` | Inventory policy | Policy authorizes action | PASS |
| Policy rejects | No Action | Invalid/denied recommendation produced no action | Inventory policy | Reasoning is not authority | PASS |
| Stale recommendation | Current state decides | Stock 5 recommendation was rejected after stock 50 observation | Inventory policy | Old context cannot blindly execute | PASS |
| Duplicate observation | No duplicate state/action | Same event ID was ignored | Inventory domain | Event identity differs from business effect | PASS |
| Loop continuation | Result event re-enters domain | Replenishment result and level event returned through transport | Inventory | Events remain the boundary | PASS |
| External request | Same authority path | External event coexisted with autonomous observation | Inventory | No second execution architecture | PASS |
| Action failure | Decision != execution success | Explicit failed result produced `FAILED` operation status | Inventory execution contract | Action outcome is observed | PASS |
| Lost result | Unknown, not failed/succeeded | Requested operation remained unresolved until explicit unknown evidence | Inventory/process | Missing result is not failure | PASS |
| No generic loop | No reusable runtime | No loop/planner/workflow abstraction introduced | Application test model | Business loop stays local | PASS |

## Autonomous behavior

Inventory owns the objective “maintain safe availability.” It observes inventory events, updates its Inventory state, asks a deterministic reasoner for a replenishment recommendation, validates that recommendation against current authoritative state and policy, creates an Action only after approval, and emits an event. A later event re-enters the same domain boundary without a direct recursive call.

This is autonomous domain behavior because the threshold and replenishment meaning belong to Inventory. It is not arbitrary planning: the test has one business objective and one known action.

## Service vs Agent comparison

A conventional event-driven service could implement `Event -> handler -> business logic -> action`. The Inventory experiment adds explicit `Observation`, `State`, `Recommendation`, `Decision`, and `Action` semantics. Those distinctions make the authority boundary inspectable: reasoning proposes, policy authorizes, and execution produces separate evidence.

The experiment does not prove that every event-driven service needs an Agent abstraction. The materially demonstrated capability is the explicit separation of advisory reasoning from deterministic policy and action semantics, plus re-entry through result events. Transport, persistence, and side-effect guarantees remain the same distributed execution concerns identified in 2Q-2T.

## Stale-context analysis

```text
Recommendation context: state version after stock = 5
Current authoritative state: stock = 50, threshold = 10
Validation mechanism: Inventory policy compares recommendation metadata to current state version
Execution decision: reject as stale; create no Action
```

The existing `Recommendation` value can carry context metadata, but it does not enforce freshness itself. Freshness is an Inventory policy concern. The experiment therefore establishes a safe domain-specific boundary without introducing a stale-recommendation manager.

## Verified

- Inventory events become Observations and update authoritative Inventory State.
- Recommendations are distinct from Decisions and Actions.
- Policy approval is required before an Action is created.
- Invalid recommendations are denied without side effects.
- A recommendation made against old state is rejected when current state has changed.
- Duplicate event identity does not create duplicate observations or actions in the Inventory component.
- Result events can re-enter the same domain behavior through transport.
- Action failure and lost results remain distinct from an approved Decision.
- External observation and autonomous observation use the same domain authority model.
- No generic agent loop, planner, workflow engine, coordinator, shared state, direct agent call, or core change was introduced.

## Inference

- Inventory is agent-like here because it continuously interprets observations through explicit reasoning and policy boundaries and can re-enter from resulting events.
- The distinction from an event-driven service is meaningful when Recommendation, Decision, and Action have separate authority and evidence semantics; it is not a claim that the underlying transport is fundamentally different.
- Recommendation freshness must be validated by the owning domain against current state. Timing alone is insufficient.
- Autonomous action execution remains subject to the operation execution contract: an approved Action does not prove a successful side effect.

## Speculation

- More complex goals may require explicit Inventory process state, authorization, operation idempotency, and reconciliation contracts already explored in earlier milestones.
- Concurrent observations may require domain-specific version or conflict rules.
- A shared recommendation-context schema might emerge, but this experiment does not justify a core primitive.

## What 2U disproved

2U disproved that reasoning can directly become execution, that an old recommendation remains valid merely because it was once produced, and that a missing autonomous result means failure. It also disproved that one autonomous domain experiment justifies a general autonomous-agent runtime.

## Open questions

- How recommendation freshness should work under concurrent observations.
- How autonomous actions receive authorization and operation identity in production.
- How autonomous side effects are reconciled after lost results.
- Whether external capability requests need additional trust or authorization semantics.
- Whether the Observation model should provide domain-level duplicate handling beyond this test-local policy.

## Assessment

**PARTIAL**

The existing architecture supports a continuous autonomous Inventory loop for a bounded business objective. The stale-context and execution-boundary tests confirm that freshness and side-effect truth remain explicit domain/process contracts. This teaches a sharper boundary than 2T, but it does not justify a generic autonomous runtime or claim general autonomous-agent support.
