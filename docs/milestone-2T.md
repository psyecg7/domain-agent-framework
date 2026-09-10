# Milestone 2T - Autonomous Domain Decision Stress Test

## Objective

2T tests whether an Order domain can determine and request the capabilities required for its own business objective through `Intent -> Capability -> Event`, without introducing a generic planner, workflow engine, coordinator, or new core primitive.

## Hypothesis

> A domain agent can autonomously determine and invoke the capabilities required to satisfy its business objective through Intent -> Capability -> Event without introducing a generic orchestration mechanism.

## Architecture

```text
Human / Agent
     -> Intent
     -> Order business semantics
     -> Capability discovery
     -> Capability invocation
     -> Invocation Events
     -> PIM / Inventory / Pricing agents
     -> Result Events
     -> Order fact interpretation
     -> Business decision
```

The test uses an Order-specific `OrderAutonomousDecision` component. It is not a generic planner: its requirements are the Order business rule that purchase evaluation needs product validity, availability, and price. `CapabilityInvoker` only resolves one capability and publishes one event.

## Test scenarios

| Scenario | Expected behavior | Observed behavior | Business owner | Architectural owner | Result |
| --- | --- | --- | --- | --- | --- |
| Autonomous discovery | Discover PIM, Inventory, Pricing ownership without side effects | Registry returns explicit owners; no events published | Order | CapabilityRegistry | PASS |
| Autonomous invocation | Invoke discovered capabilities through events | Three one-hop invocation events emitted | Order | CapabilityInvoker/transport | PASS |
| Out-of-order results | Interpret facts independent of arrival order | Pricing, Inventory, PIM produce `CAN_PURCHASE` | Order | Order fact interpretation | PASS |
| Inventory denial | Preserve Inventory authority | Order produces `CANNOT_PURCHASE` | Inventory + Order | Domain result event | PASS |
| Missing capability | Do not substitute a fact | Unknown capability stops with no event | Order | CapabilityRegistry | PASS |
| Ambiguous capability | Do not select arbitrarily | Ambiguous capability stops with no event | Order/application | Registry resolution | PASS |
| Missing result | Do not infer unavailable | Order remains `WAITING_FOR_FACTS` | Order | Process/application | PASS |
| Recommendation validation | Recommendation proposes, policy validates | Valid proposal is accepted without execution | Order | Order policy | PASS |
| Invalid recommendation | No arbitrary capability execution | Unsupported capability proposal is rejected | Order | Deterministic validation | PASS |
| Side-effect capability | Discovery is not execution | Reservation event occurs only after explicit invocation | Inventory | Capability/event boundary | PASS |
| Sequential dependency | Business rule may stop later requests | Invalid product prevents later capability invocation | Order | Order-specific logic | PASS |
| Human/agent equivalence | Same target boundary | Same invocation event shape; causation differs | Requester | CapabilityInvoker | PASS |
| No hidden generic workflow | No reusable planner mechanics | No workflow, retry, compensation, or process calls | Order | Application boundary | PASS |

## Autonomous behavior

The genuinely autonomous behavior is Order deciding what facts are required to answer its own business request, discovering who owns those capabilities, invoking them through events, and interpreting authoritative results. This is domain autonomy because the requirement set and acceptance rule belong to the Order business objective.

Order does not recreate Inventory policy. An authoritative Inventory result remains the source for availability. A recommendation cannot create an Action or invoke an arbitrary capability; it must pass Order-owned deterministic validation.

## Orchestration boundary

The experiment does not prove that several capability requests are never orchestration. Order-specific fact fan-out and the sequential rule “do not request later evidence after invalid product data” are business semantics. Generic dependency graphs, retries, timeouts, compensation, scheduling, and execution management are absent and remain outside the model.

The Order component is therefore autonomous in selecting business requirements, but it still owns coordination for this business question. Calling it an Agent does not erase that responsibility. The implementation does not contain a reusable workflow mechanism, so this is not evidence of generic orchestration infrastructure.

## LLM boundary

```text
Reasoning / Recommendation
        -> deterministic Order validation
        -> Decision
        -> Action or capability invocation
```

The tests use deterministic recommendations only. A recommendation for `customer.credit_score`, which is not an Order requirement, is rejected and emits no event. The current architecture therefore keeps probabilistic proposal separate from authoritative business execution.

## Verified

- Order can discover PIM, Inventory, and Pricing capabilities through the existing registry.
- Capability discovery has no side effects.
- Capability invocation uses the same one-hop event path used by other requesters.
- Out-of-order authoritative facts can be associated by requirement and correlation.
- Inventory denial remains authoritative.
- Missing or ambiguous capabilities do not produce substitute facts or invocation events.
- Missing results remain unresolved rather than becoming unavailable.
- Recommendations cannot directly invoke arbitrary capabilities or become authoritative actions.
- Side-effect capability discovery is distinct from invocation and domain execution.
- Human and agent requesters share the same invocation boundary.
- No direct agent calls, shared state, generic planner, coordinator, workflow engine, or core changes were introduced.

## Inference

- Domain autonomy is demonstrated at the business-requirement boundary, not as arbitrary workflow execution.
- Capability discovery is an architectural lookup; capability selection and sequencing remain Order business semantics.
- `CapabilityInvoker` remains deliberately non-orchestrating.
- A business agent can coordinate several requests without that automatically justifying a reusable orchestration abstraction.

## Speculation

- More varied Order questions may eventually require explicit application-level process semantics, as 2N-2Q already showed for long-lived side effects.
- Dynamic capability versioning, authorization, freshness, and trust may require additional domain/application contracts.
- If multiple domains independently require validated capability recommendations, a shared policy pattern might emerge, but this experiment does not justify a new primitive.

## What 2T disproved

2T disproved that autonomous capability selection means arbitrary capability execution, that capability discovery itself causes side effects, and that recommendations can safely become authoritative business actions. It also disproved that calling the requester an Agent removes the need to classify its business coordination explicitly.

## Open questions

- How dynamic capability selection should be versioned and authorized.
- Whether sequential dependencies remain business rules as request variety grows.
- How capability freshness and trust should be represented.
- Whether a reusable recommendation-validation contract will emerge across domains.
- How autonomous selection interacts with durable Order process state and reconciliation.

## Assessment

**PARTIAL**

Order autonomy works for selecting and requesting known business facts through the existing capability/event boundary. The experiment does not establish generic autonomous planning: dynamic dependencies and long-lived process execution remain explicit business/application concerns. The architecture survives without a generic orchestration mechanism, but the autonomy-versus-process boundary remains a real design boundary.
