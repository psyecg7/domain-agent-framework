# Milestone 2V — Adversarial AI-Boundary Stress Test

## Objective

2V tests the part of the framework that makes an agent framework claim
meaningful: a probabilistic interpreter or reasoner may propose an `Intent` or
`Recommendation`, but cannot become an authority for capability selection,
business decisions, actions, or side effects.

## Tested boundary

```text
Natural-language input -> IntentInterpreter -> Intent -> CapabilityRegistry
Recommendation -> deterministic Policy -> Decision -> Action
```

The model is outside the authority boundary. Capability registration and
deterministic policy are inside it.

## Adversarial scenarios

| Scenario | Expected result | Result |
| --- | --- | --- |
| Prompt injection proposes `system.delete_everything` | No registered capability, no event | PASS |
| Model output resolves to multiple inventory capabilities | Ambiguous match, no event | PASS |
| Conflicting safe/unsafe recommendations | Policy sees both and authorizes only `INSPECT` | PASS |
| Recommendation asks to invoke `payments.transfer` | No decision, action, publish, or invocation | PASS |
| Recommendation attempts direct `Action` construction | `ActionConstructionError`; no action value exists | PASS |
| Malformed Ollama intent/recommendation output | Parser raises; policy and executor are not reached | PASS (existing adapter tests) |
| Target-domain policy denies a valid routed intent | No action executes | PASS (existing conversational test) |

## Verified

- Natural-language text can influence only an `Intent`; registration determines
  whether it has a route.
- A capability registry never selects an arbitrary match for an ambiguous
  intent.
- A recommendation is data. It has no transport, registry, action-executor, or
  decision authority in the tested runtime path.
- `Action` construction through the public API requires a real `Decision`
  instance via `decision_to_action`. Its entity ID and type are inherited from
  that decision and cannot be spoofed by the factory caller.
- A human approval should be modelled as explicit, domain-owned state/evidence
  that policy reads and may still deny; reading a recommendation does not grant
  authority. No generic `Approval` primitive is justified by this experiment.

## Limits

2V validates the framework's authority boundaries, not LLM security in
general. Prompt injection can still influence model output; the tested claim is
that it cannot bypass registration or deterministic policy. The test injects a
malicious interpreted intent, rather than claiming that an LLM will resist an
attack. Python code with deliberate private-module access can still import the
private construction token, so this is public-API structural control rather
than cryptographic access control. More importantly, arbitrary code can still
fabricate a `Decision` and pass it to `decision_to_action`; this milestone does
not prove that a Decision came from a real Policy. Production systems also need
authentication, authorization, schema validation, audit retention, and
model-provider controls at their appropriate boundaries.

## What 2V disproved

2V disproved that an interpreter can invent a route, that an ambiguous model
output can select a target, and that an unsafe recommendation can construct an
Action through the public API or invoke another agent directly.

## Action-construction inventory

The repository-wide inventory before migration found ten direct `Action(...)`
sites:

| Category | Call sites | Migration |
| --- | --- | --- |
| (a) Framework default mapping | `src/agent_core/runtime/agent.py` | `decision_to_action` |
| (b) Decision-based factories | `tests/test_inventory_autonomous_domain.py`; four sites in `tests/test_agent.py`; `tests/test_agent_composition.py`; `tests/test_pharmacy_domain_stress.py`; two sites in `agent-redpanda/tests/test_runtime.py` | `decision_to_action` |
| (c) No Decision in scope | None | No discovery |

All category-(b) sites already had a `Decision` in scope and used its entity
target, so migration did not alter field values or behavior.

## Open questions

- Should `Decision` construction receive comparable structural control from a
  real `Policy.evaluate()` path? Until then, code can fabricate a `Decision`
  and construct an otherwise valid Action; this milestone intentionally does
  not attempt to close that separate authority gap.
- Same-process construction discipline is not adversarial-attacker-proof. A
  real execution security boundary would require policy decision and action
  execution to be separately deployed trust domains, with asymmetric signing,
  key provisioning and rotation, and a TOCTOU window derived from actual
  transport behavior. No cryptographic execution-token primitive is added to
  `agent-core`. The optional `agent-enterprise` reference demonstrates this
  boundary outside core with an Ed25519 Policy private key and Executor public
  key; it still requires separate deployment, durable shared replay storage,
  enterprise OIDC/JWT ingress authorization, service mTLS, and KMS/HSM key
  management to be a production control. These are explicit deployment TODOs,
  not capabilities claimed by the local development reference. Production also
  requires atomic/shared replay protection or one serialized Executor writer,
  plus a staging deployment that reruns the authorization, audit, recovery,
  and consumer-handoff checks.

## Business-state TOCTOU boundary

Cryptographic authorization proves who authorized an exact command; it does
not prove that the business state remains unchanged until execution. The
optional enterprise reference therefore signs domain-defined command
preconditions separately from action parameters. The Executor verifies their
integrity, while Inventory (or another target domain) owns the native atomic
conditional write. Its stale result is an explicit business `CONFLICT` with no
second effect, retry, or generic core CAS/state-version primitive.

The HTTP reference now accepts domain-provided preconditions during Policy
evaluation and signs them into the command delivered to the Executor. The
Executor only verifies their integrity; the owning domain must enforce the
complete atomic business condition, including both its state snapshot and the
requested quantity/capacity. Delta audit records are append transactions rather
than a read-and-overwrite table rebuild, but their retention and immutability
are external governance concerns.

The concrete `DeltaInventoryReservationHandler` reference binds this to a
native domain update: version, observed availability, and requested quantity
must all still match for a reservation to succeed. Competing authorizations
from the same snapshot have an explicit `SUCCEEDED`/`CONFLICT` split. This
reference does not claim an atomic commit spanning its stock table and the
separate 2S reservation-evidence ledger; that consistency boundary remains an
Inventory production-design responsibility.

The Postgres Inventory reference closes that specific boundary for a relational
system of record: one transaction conditionally updates stock and inserts the
operation's reconciliation evidence. A stale authorization records business
`CONFLICT`; a database error rolls both writes back. The adapter remains
Inventory-specific and does not alter `agent_core`.

### Open question: precondition negotiation

`preconditions_sha256` proves that Policy authorized one exact precondition
set; it does not define how a domain may safely refresh or negotiate a stale
set when the world changed. A binary hash comparison is deliberately strict.
Whether a target should reject, re-evaluate, accept an ETag-like replacement,
or offer another business outcome remains a domain and enterprise deployment
contract—not a generic `agent-core` protocol.
- Do multiple independent domains demonstrate enough shared approval semantics
  to justify a generic primitive? One domain-owned approval fact is not enough.
