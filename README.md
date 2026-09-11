# domain-agent-framework

This project provides a technology-independent runtime for governed domain
agents: AI may propose, but deterministic domain policy is the only authority
that can approve decisions and side effects. It is a small, generic core for
building domain agents without embedding domain-specific business logic in the
framework itself.

## What this framework is

This is not an LLM workflow engine. It is a disciplined event-driven interaction
contract for domain agents that refuses to let probabilistic reasoning become
authoritative. The central rule is: **AI advises; deterministic domain code
decides and executes.**

The package in `agent_core` defines the minimal conceptual model for a generic agent lifecycle:

Event → Observation → State → Reasoning → Recommendation → Policy → Decision → Action

It is intentionally neutral. The core is not tied to ecommerce, pharmacy, inventory, returns, or any other specific domain. It can be used for banking, logistics, manufacturing, IoT, cybersecurity, finance, data products, and many other scenarios.

## Start here

For the smallest application-oriented API, install and start with
[`agent-app`](agent-app/README.md). It provides one `AgentApp` object and
decorators for policies and action handlers while retaining the core runtime's
Decision-before-Action boundary.

The supported layering is deliberately three-tiered: **`agent-core`** supplies
the stable semantics, **`agent-app`** is the versioned ergonomic application
surface most developers use, and **adapters** provide durable state, transport,
models, and enterprise infrastructure. `agent-app` is not a second runtime or
a disposable shim; it is the public local-first application API.

Capability invocation is application composition: import `CapabilityInvoker`
from `agent_app`, not `agent_core`. It resolves a registered Intent and
publishes one Event; it never calls a target Agent directly.

Conversational routing and response rendering are application composition too:
import `ConversationalGateway` and `DeterministicResponseInterpreter` from
`agent_app`, not `agent_core`.

[`agent-conformance`](agent-conformance/README.md) is a separate test-only
package. It turns the demonstrated operation, outbox, and reconciliation
patterns into reusable CI assertions without adding runtime primitives. It
currently checks stable-operation and duplicate-delivery idempotency, restart
reconciliation, the closed reconciliation outcome vocabulary, pending-outbox
behavior after a pre-acceptance publish failure, lineage preservation,
precondition forwarding and stale-conflict behavior, atomic rollback,
duplicate-outbox protection, external idempotency identity, and explicit
unresolved-result uncertainty. It is a
callable assertion/template library, not an auto-discovered test suite; domain
teams invoke its checks from their own tests. It has no runtime dependency on
`agent-core` or `agent-app`, so the same checks apply to raw Agents, AgentApp
applications, or adapter-only domain code.

To adopt those checks in a side-effecting domain, start from the
[conformance template](agent-conformance/templates/side_effect_conformance.py)
and its [adoption checklist](agent-conformance/README.md#adopt-it-in-a-domain).
The repository also runs the helper package and its Delta reference separately
in [the conformance CI workflow](.github/workflows/conformance.yml).

For package versions, supported Python releases, and the distinction between
clean-install, unit, and infrastructure validation, see the
[release compatibility policy](docs/release-compatibility.md).

Run the repository's complete local verification with
`bash scripts/verify_framework.sh`. It runs deterministic tests first, then
starts the optional Redpanda/PostgreSQL Compose profile when Docker and the
required Python drivers are available; otherwise it reports that infrastructure
validation was skipped. Set `VERIFY_INFRA=0` to run only deterministic checks.

For package-index distribution, follow the [publishing guide](docs/publishing.md).
The manual TestPyPI workflow uses GitHub OIDC trusted publishing and does not
store a package-index token in this repository.

For the production boundary—what is verified here, what requires domain or
platform controls, and what is intentionally not claimed—read the
[production safety case](docs/production-safety-case.md) before deploying a
side-effecting agent.

For payment providers, devices, or third-party APIs, also follow the
[external side-effect boundary](docs/external-effect-boundary.md): the
framework requires stable idempotency identity and reconciliation, not guessed
retries after a lost provider result.

For a local prototype, you need one app, one policy, and one event:

```python
from agent_app import AgentApp
from agent_core import Event

app = AgentApp()

@app.policy("measurement.received")
def decide(state):
    if state.values.get("temperature", 0) > 40:
        return app.decide("INVESTIGATE", severity="MEDIUM", reason="threshold exceeded")

@app.action("INVESTIGATE")
def notify(action):
    print(action.action_type)

app.process(Event("measurement.received", "sensor-1", "sensor", {"temperature": 42}, source="telemetry"))
```

`app.decide(...)` returns a target-free `DecisionSpec`. `AgentApp` binds its
target from the explicit `state` argument passed to the policy, so no
event-local, thread-local, or mutable current-entity context exists.

The framework applies the event to state, invokes deterministic `decide()`,
creates Actions only from the resulting Decisions, and then invokes
the registered action handler. Returning no decision means no action runs. See the
[developer guide](docs/developer-guide.md) for the one-page path from this
local setup to durable stores, transport, AI advice, and high-risk execution.

## Core lifecycle

The runtime is built around a sequence of explicit steps:

1. An `Event` is emitted when something happens.
2. The event is converted into one or more generic `Observation` objects.
3. The observations are applied to an `Entity`'s current `State`.
4. An optional `Reasoner` produces non-authoritative `Recommendation` objects from the state and context.
5. Deterministic policy evaluation turns the state and, when supported, recommendations into `Decision` objects.
6. `Decision` objects can be converted into generic `Action` instructions.

## Deterministic policy evaluation vs AI reasoning

This core draws a clear line between deterministic policy evaluation and AI-driven reasoning.

- Deterministic policies are plain Python callables and rule checks. They are explicit, inspectable, and do not rely on an LLM.
- The core defines the `Policy` interface and decision semantics, but actual business logic such as inventory threshold checks, pricing rules, fraud constraints, or risk policies belongs in the domain application layer.
- A `Reasoner` is a generic adapter boundary that returns advisory recommendations. It cannot authorize or execute actions; deterministic policy remains authoritative.

The adversarial checks in [Milestone 2V](docs/milestone-2V.md) cover unsafe,
ambiguous, and prompt-influenced model outputs. `Action` cannot be constructed
through the public API without a real `Decision` instance: callers must use
`decision_to_action(decision, ...)`, which inherits the decision's entity
target. This is enforced by the type rather than convention or test coverage.

This does **not** prove that every Action is authorized by a real policy:
arbitrary code can still construct a `Decision` and pass it to the factory, and
Python code with deliberate private-module access can import the construction
token. Decision construction is an explicit open authority-boundary question.
The checks establish authority boundaries—not a claim that prompting alone
makes a model secure.

For a separately deployed Policy service and Executor service, the optional
[`agent-enterprise`](agent-enterprise/README.md) reference uses an asymmetric
Ed25519 authorization. Policy holds the private key; Executor holds only the
public key and verifies a short-lived, replay-protected authorization bound to
the Decision ID, action target/type, parameters, idempotency key, and executor
audience. It can also reject a decision after revocation and require the
receiving Executor to declare its owned action types. Multi-replica deployments
use the optional PostgreSQL replay/revocation stores and still need a tested
revocation-propagation SLO. This is intentionally outside `agent-core`: it is
meaningful only across real deployment trust domains.

An [enterprise security Compose profile](docs/enterprise-compose.md) provides
disposable Keycloak, Vault, and step-ca services for local integration. It is
explicitly not a production identity, CA, or key-management deployment.

## Domain-owned approval evidence

Human or Compliance approval can be represented as an ordinary, domain-owned
fact—not a pre-policy gate. In [Milestone 2W](docs/milestone-2W.md), Order
requests Compliance approval through the same capability/event boundary as
PIM, Inventory, and Pricing; its policy evaluates that evidence alongside the
other facts and may still deny the purchase. This is a concrete domain pattern,
not a generic `Approval` primitive.

This keeps the runtime technology-independent while still allowing adapter-based extension.

The framework is not a business-rules framework by itself. Domain-specific policies live outside the core in the application that uses it.

## Purpose of the ports

The ports defined in `agent_core.ports` are infrastructure boundaries expressed as `Protocol` interfaces:

- `StateStore`: loads and persists state.
- `PolicyEngine`: evaluates state and produces decisions.
- `Reasoner`: produces advisory recommendations from a generic reasoning context.
- `ActionExecutor`: executes generated actions.
- `MemoryStore`: optionally retrieves semantic context without replacing authoritative state.

These interfaces allow adapters to be plugged in later without coupling the core to a database, queue, model provider, or web framework.

## Minimal usage example

If you need the raw runtime without `agent-app`'s ergonomic surface, construct
`Agent` directly as shown below. This is the advanced API; new applications
should start with `AgentApp` above.

```python
from datetime import datetime
from agent_core import Agent, Event, Policy, Decision

class ThresholdPolicyEngine:
    def evaluate(self, state):
        value = state.values.get("value")
        if isinstance(value, (int, float)) and value > 40:
            return [
                Decision(
                    entity_id=state.entity_id,
                    entity_type=state.entity_type,
                    decision_type="INVESTIGATE",
                    severity="MEDIUM",
                    reason="Value exceeds threshold",
                )
            ]
        return []

class InMemoryStateStore:
    def __init__(self):
        self._states = {}

    def get(self, entity_id, entity_type):
        return self._states.get((entity_id, entity_type))

    def save(self, state):
        self._states[(state.entity_id, state.entity_type)] = state

store = InMemoryStateStore()
agent = Agent(store, ThresholdPolicyEngine())

event = Event(
    event_type="measurement_received",
    entity_id="sensor-1",
    entity_type="sensor",
    payload={"value": 42},
    source="telemetry",
)

result = agent.process(event)
print(result.state.values)
print(result.decisions)
print(result.actions)
```

## Event-driven composition

Independent domain agents compose through ordinary `Event` values. An action may publish a resulting event through a transport such as Redpanda, and another generic `Agent` can process that event with its own state store and policy. Agents do not call or import one another. Event metadata can carry generic `correlation_id` and `causation_id` values.

## Capabilities and intents

An agent may expose immutable, declarative `Capability` objects through `Agent.capabilities()`. An `Intent` describes a requested operation, and `CapabilityRegistry` can discover matching capabilities. Discovery does not authorize or execute anything: invocation remains an event-driven request, and the target agent retains authority over its own state, policy, decisions, and actions.

The optional conversational boundary in `agent-ollama` translates natural language into an `Intent` through `IntentInterpreter`. It cannot invent capabilities or call agents. A deterministic application-layer gateway resolves exactly one registered capability before emitting an event; unknown or ambiguous matches stop safely.

## Multi-domain conversational routing

A single domain-agnostic conversational gateway can route to capabilities owned by multiple independent agents. Inventory, product-information, and order capabilities can coexist in the same `CapabilityRegistry`; the gateway interprets the request, resolves exactly one registered capability, and emits an invocation `Event`. The target agent remains authoritative over state, policy, decisions, and actions.

This is capability routing, not orchestration. The gateway does not execute domain operations, coordinate multi-step workflows, inspect results to call another domain, or contain domain-specific routing logic. Capability discovery is not authorization.

## The complete conversational loop

The conversational interface supports a complete request and response path:

```text
Human request
    -> IntentInterpreter -> Intent -> CapabilityRegistry
    -> invocation Event -> Domain Agent -> Policy -> Decision -> Action
    -> result Event -> ResponseInterpreter -> Human response
```

The `agent_app.ConversationalGateway` is an application-level boundary. It
translates human requests into domain events and domain result events into human
responses. It does not own domain state, policy, decisions, actions, or
workflows, and it never reads a domain store or calls a domain agent directly.

Result events use the generic `Event` primitive. A capability may declare its result event type through metadata such as `{"result_event_type": "inventory.availability.result"}`. The gateway correlates a result only when its event type and `metadata["correlation_id"]` match a pending request. `correlation_id` remains stable across the interaction; `causation_id` identifies the immediate request, action, or result that caused the next event.

`ResponseInterpreter` is separate from `IntentInterpreter`:

- `IntentInterpreter` translates a human request into an `Intent`.
- `ResponseInterpreter` translates a matched result `Event` into presentation text.

The deterministic response interpreter uses explicitly registered formatters, so domain-specific wording remains outside the gateway. Unknown result types, malformed payloads, missing lineage, and unmatched correlations are ignored without invoking an agent, creating an action, or mutating state. Policy denial remains authoritative and can be represented by a domain-owned result event.

The domain agent remains authoritative from invocation through execution. Adding a domain requires registering its capability and result formatter; it does not require modifying the conversational gateway. Result events are the return channel, and the conversational layer does not inspect domain state directly.

## Agent-to-agent capability invocation

An agent may request another agent's capability using the same `Intent -> CapabilityRegistry -> Event` mechanism used by the conversational interface:

```text
Human -> IntentInterpreter -> Intent -> CapabilityRegistry -> Event -> Agent
Agent -> Intent                 -> CapabilityRegistry -> Event -> Agent
```

`CapabilityInvoker` is an application-level requester. It resolves exactly one capability and publishes a generic `capability.invocation` `Event` containing the capability ID and intent. Transport subscriptions route that event to the capability owner. The invoker does not import or call the target agent, inspect its state, or create its decisions or actions.

Capability ownership is declarative metadata, for example `{"owner": "inventory"}`. Discovery does not grant authorization: the target agent remains authoritative over its own state, policy, decisions, and actions. The target returns a normal result `Event`, preserving the request `correlation_id` and immediate `causation_id` through the existing lineage model.

This milestone intentionally proves one hop only: one request, one capability, one target agent, and one result. It does not implement recursive invocation, planning, workflow orchestration, A2A, MCP, RPC, or direct agent APIs. Agents communicate through ordinary events, not a special agent-to-agent protocol.

## Real Domain Validation - Pharmacy Commerce

The pharmacy stress test keeps the business slice deliberately small and leaves all domain concepts outside `agent-core`:

```text
PIM       -> product identity and information
Inventory -> stock and availability
Pricing   -> commercial price information
Order     -> order and order validation state
```

The concrete interaction is an Order-domain request to determine whether product `P123` can be purchased:

```text
Order Agent
    -> Intent: inventory availability
    -> CapabilityRegistry
    -> inventory.availability.check
    -> capability.invocation Event
    -> Inventory Agent
    -> Inventory state and local policy
    -> Decision and Action
    -> inventory.availability.result Event
    -> Order Agent
```

The Order Agent requests the Inventory capability; it does not own or read inventory state. The Inventory policy allows availability when stock is positive and denies it when stock is zero. The positive and negative paths use separate domain stores, preserve correlation and causation metadata, and do not create a false availability action on denial. PIM, Inventory, Pricing, and Order capabilities are registered with generic ownership metadata, while only the required Order-to-Inventory interaction is exercised.

This differs from an Order service that synchronously calls PIM, Inventory, and Pricing implementations because the request crosses an event boundary and capability ownership remains with the target domain. The test does not claim that this is automatically superior to service-based architecture. It demonstrates domain ownership, capability-based discovery, event decoupling, local policy authority, no shared state, and no direct agent calls. A Supplier capability may be registered, but the Inventory result does not invoke it: capability discovery is not workflow planning.

## 2K Assessment

### A. Is this genuinely event-driven?

Within this test, yes. The Order requester publishes an invocation `Event`, transport routing delivers it to Inventory, and Inventory publishes a result `Event` consumed by Order. The in-memory transport is synchronous for test determinism, but the dependency is still event-shaped; a broker adapter can provide asynchronous delivery.

### B. Is the Order Agent an orchestrator?

No. It creates one Intent for one capability and consumes one result. It does not discover or invoke a second capability, coordinate a workflow, inspect another domain's state, or decide Inventory policy.

### C. Is `CapabilityInvoker` an orchestrator?

No. `CapabilityInvoker` resolves exactly one capability and publishes exactly one event. It does not call agents, interpret results, retry, plan, chain capabilities, or manage workflow state. It is a capability-discovery and event-publication helper at the application boundary.

### D. Does the Capability Registry become a service registry?

It is a declarative capability index: it answers which registered capabilities match an Intent. It does not own agent instances, route messages, authorize requests, execute actions, inspect state, or manage service health and deployment. Ownership metadata helps application transport routing but does not turn discovery into execution.

### E. Does this reduce to microservices with different terminology?

There is a strong argument that it can. Independent stores, policies, actions, and event transport resemble conventional event-driven services, and the capability layer can be viewed as a semantic contract over service endpoints. The counterpoint is that the framework makes Intent, Capability, Decision, and policy authority explicit generic semantics rather than leaving them implicit in service APIs. This milestone does not prove a fundamentally new distributed-systems model.

### F. What architectural property is genuinely new?

The demonstrated property is improved decoupling through capability-based discovery combined with explicit local policy authority. The honest claim is not novelty: the architecture provides a reusable semantic interaction model for domain agents without direct calls or a central workflow engine.

### G. What is still missing for production?

Concrete gaps include durable asynchronous transport, delivery and retry semantics, idempotency and deduplication, authentication and authorization, schema/version governance, observability, deadlines and failure handling, persistence consistency, and operational deployment concerns. The pharmacy test does not address payment, shipment, prescription validation, or customer identity.

## Multi-Domain Composition Stress Test - 2L

The 2L experiment asks what happens when `Can I buy P123?` requires three authoritative facts:

```text
PIM       -> product is commercially active
Inventory -> product has stock
Pricing   -> product has a positive price
```

The Order-side application requests the three capabilities independently through the existing `CapabilityInvoker`. Each request is an ordinary `capability.invocation` event with the same correlation ID. PIM, Inventory, and Pricing process their own state and policy and publish independent result events. The test deliberately delivers those results in the order Pricing, PIM, Inventory to show that sequential domain execution is not required.

The final Boolean answer is assembled by an explicit test-local `OrderPurchaseAssessment`, which owns the business question and waits for the three terminal facts. It is not a new framework primitive or a hidden generic coordinator. This is the honest architectural finding: once one interaction requires multiple independent facts, some component must own completeness, aggregation, and the final business answer. In this experiment that responsibility belongs to Order, so the Order-side application is coordinating a business assessment even though it does not directly call domain agents or own their state.

Aggregation is distinct from action orchestration here. The assessment combines terminal facts with `active AND in_stock AND priced`; it does not choose retries, compensate actions, discover follow-up capabilities, or traverse a capability graph. A missing result produces no final answer. A denied domain produces a negative final answer after the other terminal results arrive. No generic retry or failure coordinator is introduced.

### 2L Assessment

#### Verified

- PIM, Inventory, Pricing, and Order use separate state stores and capability ownership metadata.
- Order can publish three independent capability requests through the same registry and invoker used by the one-domain interaction.
- Domain results can arrive out of order and retain the originating correlation ID and causation lineage.
- Each domain's local policy controls its own positive or negative result.
- A missing result does not fabricate a final business answer.
- Receiving a result does not automatically invoke Supplier or any other capability.
- No direct agent calls or cross-domain state reads are required by the experiment.

#### Inference

- The current primitives support decentralized domain execution but not the ownership of a multi-result business conclusion by themselves.
- A multi-domain business question can remain event-driven while still having an explicit business aggregation responsibility.
- Combining facts is not automatically workflow orchestration; coordinating retries, ordering, compensation, or follow-up actions would be orchestration.
- The conversational entry point and Order-agent entry point can share capability discovery and event publication, but neither path removes the need to decide who owns multi-result aggregation.

#### Open Question

- Whether the final `CanBuy` assessment is properly an Order-owned business capability or should be modeled as another legitimate domain-owned capability remains a domain-design decision. Creating a generic `CommerceCoordinator` would currently be infrastructure orchestration, not a demonstrated business domain.
- The in-memory experiment does not settle production semantics for timeouts, duplicate results, version conflicts, stale facts, retries, or durable aggregation.
- This experiment does not prove that the architecture is materially different from conventional event-driven microservices with a capability vocabulary. Its strongest demonstrated property is explicit ownership and decoupled interaction; its limitation is that multi-domain conclusions still require an explicit coordinating responsibility.

#### What 2L Disproved

It disproved the stronger claim that capability events alone make multi-domain composition fully decentralized. The framework prevents direct calls and automatic chaining, but it does not eliminate the need for an owner of completeness and aggregation when one business answer depends on several domains.

## 2M Assessment - Business Action Coordination

### Scenario

The 2M experiment models the smallest useful `Buy P123` action. PIM validates the product, Inventory checks availability, Pricing validates the price, Order creates the order, and Inventory reserves stock. Payment, shipping, prescription handling, authentication, and other pharmacy concerns are deliberately excluded.

### Domain ownership

```text
PIM       -> product validity and product state
Inventory -> stock, availability, and reservation action
Pricing   -> price validity and price state
Order     -> order creation and order lifecycle
```

Only Inventory can execute reservation, and only Order can execute order creation. No shared transaction state or cross-domain state access is introduced.

### Execution model

The Order-side application first issues three explicit Intents through `CapabilityInvoker` for product validation, availability, and price. Their invocation events may be delivered independently and their result events may arrive late or out of order. Once all three facts are terminal and positive, it explicitly issues two more Intents: `order.create` and `inventory.reservation.create`. Those actions are handled by their owning domain agents and return result events with the original correlation ID and immediate causation ID.

### Coordination analysis

The test-local `PurchaseCoordinator` is intentionally an explicit Order-side process responsibility. It tracks completeness, decides when action requests may begin, waits for action results, and emits the final purchase result. It is not a new framework primitive and it does not call agents directly, but it is coordinating a business process. The experiment therefore does not pretend that the multi-action flow is fully decentralized.

The responsibility classification is:

```text
Domain authority       -> PIM, Inventory, Pricing, Order
Fact aggregation       -> Order-side purchase assessment
Business decision      -> Order decides whether prerequisites permit actions
Process coordination   -> Order-side application starts and correlates stages
Transport              -> Event transport delivers events
```

### Orchestration analysis

Yes, the Order-side component is an orchestrator in the meaningful process sense once it chooses the stage boundary, waits for multiple results, starts order creation and reservation, and determines final completion. Calling it an Agent would not change that classification. This can be legitimate Order-owned business responsibility, but it is not evidence that orchestration has disappeared.

The `CapabilityInvoker` itself is still not an orchestrator: it resolves one capability and publishes one event. It does not retry, wait, compensate, deduplicate, or infer the next step. The coordination exists in the explicit application/domain component above it.

### Failure semantics

Policy denial before side effects prevents the corresponding domain action and produces a negative business result. If Order creation is denied while reservation is independently allowed, reservation can still succeed in this experiment, exposing partial completion. If reservation is denied after order creation succeeds, the final purchase result is negative while the order action remains completed. If order creation fails after reservation succeeds, the reservation remains successful and no automatic `inventory.release` compensation is emitted.

These are deliberately exposed failures, not generic framework behavior. The
current architecture has no generic transaction, compensation, rollback,
timeout, or durable process-state primitive. The tested Order reference does
show the safe recovery shape: Order explicitly decides to release, persists a
stable release request before publication, and waits for Inventory's result.
If publication fails before broker acceptance, the request remains pending
across restart; duplicate delivery produces only one Inventory release effect.
The policy for retrying, reconciling a missing result, or choosing another
recovery action remains domain-owned.

When a release request may have been accepted but its result is missing, Order
uses the Inventory-owned `inventory.reservation.release.reconcile` capability.
Inventory answers from durable operation evidence with `EXISTS`, `ABSENT`,
`CONFLICT`, or `STILL_UNKNOWN`. Only `EXISTS` confirms release recovery; the
other outcomes remain visible to Order for an explicit retry decision or
escalation. This is demonstrated by
[`test_pharmacy_process_vs_workflow.py`](tests/test_pharmacy_process_vs_workflow.py)
and
[`test_pharmacy_release_reconciliation.py`](tests/test_pharmacy_release_reconciliation.py),
not provided as a generic reconciliation engine.

### Idempotency

`agent-core` does not enforce reservation idempotency: `CapabilityInvoker`
may publish duplicate requests and `Agent` may create an Action for each
accepted event. The Order/Inventory reference demonstrates the correct domain
pattern—Inventory keys its business effect by stable operation ID and treats a
duplicate release as one effect—but that is conformance evidence, not a
framework guarantee. Inventory owns the business invariant; transport may also
need duplicate-delivery controls. The experiment does not add an idempotency
framework.

### Verified

- Product validation, availability, price validation, order creation, and reservation are represented by separately owned capabilities.
- Cross-domain actions use the existing Intent, registry, Event, Agent, policy, decision, action, and result-event path.
- Inventory reservation is executed only by the Inventory-owned handler; Order never mutates inventory state.
- Policy denial prevents the denied action.
- Late and out-of-order results remain correlated.
- Partial completion is observable after a later failure.
- Duplicate reservation delivery is not automatically made safe.
- No direct agent calls, shared transaction state, automatic chaining, saga, workflow engine, or new dependency was added.

### Inference

- Domain autonomy and process coordination are separate responsibilities. Event boundaries preserve domain authority but do not remove process ownership.
- Order can legitimately own a purchase decision, but coordinating action sequencing and completion makes it an orchestrator for this interaction.
- Reservation idempotency is primarily an Inventory business safety concern, with transport duplicate delivery as a related infrastructure concern.
- The resulting design is materially similar to conventional event-driven distributed services, with explicit capability and policy semantics layered over the interaction.

### Open Question

- Whether purchase coordination belongs inside the Order domain or requires a separately modeled business-process domain is not settled by this experiment. A generic coordinator would currently be infrastructure, not a demonstrated business owner.
- `agent-core` deliberately does not supply a generic process, outbox,
  compensation, or recovery engine. The Order test reference now demonstrates
  one explicit release-recovery path, including a failed pre-acceptance publish
  and restart, but its durable store and recovery policy remain application
  responsibilities.
- The current `Action` contract does not carry a generic request idempotency key automatically; a domain-specific reservation contract would need to define and enforce one.

### What 2M Disproved

2M disproved the claim that capability/event interaction alone makes a cross-domain business action decentralized and transactionally safe. The framework supports explicit event-driven coordination and preserves local authority, but it does not eliminate the need for a process owner, compensation semantics, durable state, or idempotency policy once side effects span domains.

## 2N Assessment - Process Semantics

### Problem exposed by 2M

2M showed that one purchase can produce a successful reservation and a failed order operation. A capability invocation and an event lineage are not enough to represent what remains true after that partial completion, who decides recovery, or how a duplicate operation is identified. 2N investigates those semantics without building a workflow engine.

### Minimum process semantics

The smallest useful Order-owned `PurchaseProcess` record in the experiment contains:

```text
process_id       identity of one purchase attempt
order_id         Order domain identity
product_id       requested product
correlation_id   event lineage for the interaction
status           PENDING_FACTS, READY, EXECUTING, COMPLETED,
                 FAILED, PARTIALLY_COMPLETED, or RECOVERY_REQUIRED
facts            terminal domain outcomes, not copied domain state
operations       stable operation IDs and attempt/result status
event ledger     applied event IDs for duplicate delivery
recovery reason  why explicit recovery is required
```

The process is not an `Agent`, not a generic `Process` primitive, and not a workflow runtime. It observes result events and records the lifecycle of an Order business interaction.

### Process ownership

The purchase process belongs to the Order application/domain because it represents the lifecycle of an Order-owned purchase attempt. PIM still owns product validity, Inventory owns stock and reservation, and Pricing owns price validity. The process remembers that those domains returned terminal outcomes; it does not store their stock, price, or product state as a second authority.

### Domain state vs process state

Domain state answers questions such as `how much stock exists?` or `what is the current price?`. Process state answers questions such as `which required result events have been observed?`, `which operation succeeded?`, and `does this purchase require recovery?`. These are different responsibilities. The test process has no domain store dependency and stores no inventory quantity.

### Failure semantics

If all required facts are positive, the process becomes `READY`, then `EXECUTING` after an explicit Order decision. If both side-effect operations succeed, it becomes `COMPLETED`. If one succeeds and the other fails, it becomes `PARTIALLY_COMPLETED`; it is not silently reported as either success or an ordinary failure. A later explicit Order recovery decision moves it to `RECOVERY_REQUIRED`.

### Recovery semantics

Failure detection and recovery decision are separate. A partial process records the successful operation and the failure, but it does not release inventory, retry, or compensate automatically. The experiment leaves recovery with an explicit Order business decision. Whether that decision should request a domain-owned compensation capability or remain manual is unresolved; generic infrastructure must not invent it.

### Timeout semantics

The experiment models timeout as an explicit semantic observation: a required operation with no result is marked `TIMED_OUT`, and the process becomes `RECOVERY_REQUIRED`. No scheduler or timer infrastructure is introduced. Absence alone is not treated as failure until an explicit timeout decision exists.

### Retry semantics

A temporary failure leaves the process executing and marks the operation `TEMPORARY_FAILURE`. An explicit retry decision returns that operation to `PENDING` and increments its attempt count. `CapabilityInvoker` does not retry, and no generic retry policy is added. Whether a retry is safe is a domain and operation decision, especially for reservation.

### Idempotency semantics

The identity of a business operation is the stable operation ID, derived from the process and operation kind, for example `process-1:inventory.reserve`. It is not the correlation ID, event ID, or generated Action ID. Duplicate result events with the same event ID are ignored, and duplicate operation registration does not create a second logical operation. The current production primitives still do not enforce idempotency at action execution; Inventory must define the safety contract for duplicate reservation requests.

### Persistence semantics

The experiment reconstructs a fresh Order process from a snapshot containing identity, facts, operation ledger, applied event IDs, status, and recovery reason. Those values must survive a process, agent, or consumer restart if delayed and duplicate events are to be handled consistently. This test uses an in-memory store only; it does not choose a database or add a persistence adapter.

### Core impact

`agent-core` required no modification. The semantics are currently specific to a pharmacy purchase and Order lifecycle, so a test-local/application-level model is more honest than introducing `Process`, `ProcessState`, or `ProcessManager` into the generic core. A status field or process ID alone does not establish a domain-independent primitive.

### Verified

- A purchase process needs identity distinct from correlation, event, causation, and action identities.
- Process state can record terminal domain outcomes without duplicating domain state.
- Out-of-order facts, duplicate events, delayed results, temporary failures, and explicit timeouts can be represented locally.
- Partial completion is a distinct semantic condition and can require recovery.
- Recovery and retry do not happen automatically.
- Stable operation identity is distinct from generated execution identity.
- A snapshot can reconstruct process status and deduplicate replayed events.
- No direct agent calls, shared transaction state, workflow engine, saga, retry framework, compensation framework, or core change was needed.

### Inference

- The purchase process is a legitimate Order-domain concept, while transport delivery and event identity remain infrastructure concerns.
- Process semantics are partly business-specific: the meaning of reservation recovery and order completion belongs to the purchase domain, even though durable storage and delivery guarantees would be infrastructure support.
- A process record is not automatically a workflow engine. It becomes workflow infrastructure when generalized with reusable scheduling, retry, compensation, and execution semantics.
- The architecture remains materially similar to event-driven distributed services; its useful distinction is the explicit separation of domain authority, business process state, and transport execution.

### Open Question

- Whether a production purchase process needs a durable journal rather than snapshots, and how event versioning should work, remains unresolved.
- Inventory must define whether operation IDs are sufficient for reservation idempotency or whether a domain-specific reservation key is required.
- The owner and semantics of compensation after partial completion are not settled. The current architecture can detect recovery need but does not decide or execute recovery.
- Timeout deadlines, retry limits, stale-result handling, and crash recovery need concrete operational contracts before production use.

### What 2N Disproved

2N disproved the assumption that correlation IDs and one-hop result events alone are sufficient process semantics. Long-lived cross-domain interactions also need a business-process identity, operation identity, applied-event memory, explicit status, and recovery meaning. It also disproved the idea that these findings justify a generic Process primitive automatically: the demonstrated semantics remain tied to the Order purchase domain.

## 2O Assessment - Process vs Workflow Boundary

### Scenario

The 2O experiment revisits `Buy P123` after Inventory reservation succeeds and Order creation fails. It also simulates process restart, duplicate reservation delivery, temporary failure, and a missing reservation result. The process remains event-driven and uses the existing Order-owned 2N process model.

### Business Semantics

The business facts are: Inventory has reserved stock, or has not; an Order exists, or does not; and a purchase may therefore require recovery. `inventory.reservation.release` is an Inventory business capability requested by an explicit Order recovery decision. It is not an automatically inferred compensation step.

Inventory owns the invariant that the same business reservation operation must not reserve stock twice. Order owns the meaning of a purchase that has a reservation but no completed order. Those are domain responsibilities, not generic event mechanics.

### Process Semantics

The Order-owned process remembers `process_id`, `order_id`, `product_id`, `correlation_id`, required fact outcomes, stable operation IDs, operation status, applied event IDs, and recovery reason. It can represent `PARTIALLY_COMPLETED` and `RECOVERY_REQUIRED`, reconstruct itself from a snapshot, ignore duplicate result events, and retain a temporary failure until an explicit retry decision.

The process does not own stock, price, product data, or order state. It observes domain outcomes. Process identity is distinct from correlation identity, event identity, causation identity, and generated action identity. Operation identity is the stable identity relevant to one intended reservation or order operation.

### Infrastructure Semantics

Transport delivery, event duplication, persistence mechanics, process reconstruction, runtime restart, and physical timeout measurement are infrastructure or execution concerns. The tests simulate them deterministically; they do not add a scheduler, persistence adapter, retry manager, compensation engine, or workflow runtime.

The experiments distinguish three kinds of retry: transport retry concerns redelivery, operation retry repeats an explicitly identified operation, and business retry is an Order decision that a retry is appropriate. They are not interchangeable. Similarly, a timeout measurement may be supplied by infrastructure, while the decision that a missing reservation requires recovery is process/business semantics.

### Verified

- Reservation success followed by Order failure is represented as partial completion, not false purchase success.
- A restarted process preserves process identity, operation identity, completed outcomes, and duplicate-event history.
- Duplicate reservation requests are rejected by the Inventory-owned reservation invariant in the test; this is not generic event deduplication.
- Temporary failure requires an explicit retry decision.
- A missing result does not create success; an explicit timeout observation moves the process to recovery-required.
- Recovery remains event-driven when Order explicitly requests the Inventory release capability.
- The process does not read domain stores, duplicate authoritative domain values, or call agents.
- No generic workflow, saga, scheduler, retry, timeout, compensation, or process-manager abstraction was added.

### Inference

- The Order process remains a legitimate business-process concept because its recorded meanings are purchase-specific: reservation without order, recovery-required purchase, and business retry choice.
- Idempotency has two layers: Inventory owns the business invariant against duplicate reservation, while infrastructure may redeliver events and the process may remember applied event IDs.
- Persistence and restart support are execution requirements for preserving business-process semantics, but the storage mechanism does not belong in `agent-core`.
- The current design is still close to conventional event-driven distributed services; the meaningful distinction is explicit separation of domain authority, process meaning, and infrastructure mechanics.

### Open Questions

- Whether production recovery should request Inventory release, await manual intervention, or apply another Order policy remains unresolved.
- The architecture does not yet define durable journaling, timeout deadlines, stale-result handling, retry limits, or operation-key propagation into generated Actions.
- It remains open whether some process execution support should eventually be a separate application infrastructure package. The tests do not justify a generic core primitive.

### What 2O Disproved

2O disproved that a process status and correlation ID alone are enough to make partial cross-domain actions safe. It also disproved that duplicate event handling automatically provides reservation idempotency, or that recovery should be inferred as generic compensation. Detecting recovery, deciding recovery, and executing a domain recovery action are separate responsibilities.

### Core Impact

`agent-core` was unchanged. The experiment did not demonstrate a stable domain-independent meaning that could not be expressed with existing `Event`, `Intent`, `Capability`, `Decision`, `Action`, and application-level process records. Adding a generic `Process`, `Workflow`, or `ProcessManager` now would create premature coupling and hide the unresolved ownership questions.

### Architectural Conclusion

The Order-owned process remains a legitimate business-process concept, not a generic workflow runtime, as long as it records and interprets purchase semantics. It approaches workflow infrastructure when it accumulates generic scheduling, retry policy, compensation registries, dependency graphs, or execution management. The current boundary is therefore explicit: Order owns purchase meaning and recovery decisions; Inventory owns reservation safety; infrastructure owns delivery, persistence, restart, and physical timing mechanics.

| Responsibility | Business Process | Infrastructure |
| --- | --- | --- |
| Inventory reservation meaning | Yes | No |
| Order creation meaning | Yes | No |
| Purchase progression | Yes | No |
| Recovery-required state | Yes | No |
| Duplicate operation identity | Yes, process records it | No |
| Inventory idempotency invariant | No, Inventory owns it | No |
| Event delivery retry | No | Yes |
| Persistence mechanism | No | Yes |
| Runtime restart | No | Yes |
| Business retry decision | Yes, Order decision | No |
| Timeout measurement | No | Yes |
| Compensation meaning | Order/Inventory business decision | No generic answer |

The experiment did not justify a new abstraction or a next architectural milestone. The unresolved production questions should be answered with concrete operational and domain contracts before generalization.

## 2P - Business Process Generality

### Scenario

The PIM experiment models publication of product `P123` version `v7`: product identity and content are validated, then the product is published to independent catalog and search targets. It is intentionally modeled without copying the Order purchase process or adding a new domain.

### Independent PIM Semantics

`ProductPublicationProcess` is a PIM-specific process representation. It uses PIM vocabulary and lifecycle states: `PENDING_VALIDATION`, `READY_TO_PUBLISH`, `PUBLISHING`, `PUBLISHED`, `PARTIALLY_PUBLISHED`, `CORRECTION_REQUIRED`, and `RECOVERY_REQUIRED`. A publication operation is target- and product-version-specific, such as `publication-1:v7:catalog`.

Validation results may arrive out of order. Publication targets may complete independently. A catalog publication can succeed while search publication fails, producing partial publication rather than false overall success. A corrected product version is a separate publication attempt, not a mutation of the original process.

### Business Semantics

PIM business meaning includes whether a product version is valid, whether it is ready for publication, which publication targets completed, and whether incorrect or incomplete publication requires correction or recovery. Duplicate publication protection is a PIM/publication-target business invariant, not generic event deduplication. Recovery may mean correcting product content, retrying a target, or requesting a target-specific operation; the process does not infer which choice is correct.

### Process Semantics

The PIM process independently requires a process identity, product/version identity, correlation ID, validation outcomes, stable target operation IDs, operation statuses, applied event IDs, and recovery reason. It can reconstruct progress after restart, ignore a replayed event, retain a pending target when no result arrives, and reject a publication result that has no recorded request.

### Infrastructure Semantics

Event persistence, transport delivery, duplicate delivery, storage, runtime restart, and timeout measurement remain execution concerns. The PIM process records event identities and interprets explicit observations, but it does not schedule timers, retry automatically, call agents, or own publication target state.

### Order vs PIM Comparison

| Semantic | Order purchase | PIM publication | Common? |
| --- | --- | --- | --- |
| Process identity | Purchase attempt | Product-version publication attempt | Yes, business-process identity |
| Operation identity | Order creation/reservation | Target publication | Yes, operation identity |
| Observed outcomes | Product, stock, price, actions | Validation and target results | Yes, observed process outcomes |
| Progress | Ready, executing, complete | Validating, publishing, published | Partial: concept common, states differ |
| Partial completion | Reservation without order | One target published, another failed | Yes, meaning is domain-specific |
| Recovery | Purchase recovery/release decision | Correction, retry, or target recovery | Partial: recovery state common, action differs |
| Duplicate operation handling | Inventory reservation invariant | Publication target invariant | Partial: identity pattern common, invariant differs |
| Business retry | Order decision | PIM correction/publication decision | Domain-specific |
| Compensation | Reservation release candidate | Retract/correct publication candidate | Domain-specific |
| Timeout | Missing purchase result | Missing publication target result | Process/infrastructure boundary, policy differs |

### Verified

- PIM independently requires process and operation identity for meaningful publication lifecycle tracking.
- PIM can represent validation, publication progress, partial publication, recovery-required, restart reconstruction, duplicate delivery, out-of-order results, and missing results without a generic Process primitive.
- PIM process state does not contain authoritative product content or publication target state.
- PIM recovery semantics are correction/publication decisions, not Inventory-style reservation compensation.
- Order and PIM can be compared by evidence without sharing a process base class or changing `agent-core`.

### Inference

- There is partial generality: process identity, operation identity, observed outcomes, progress tracking, duplicate-event memory, restart reconstruction, and recovery-required state recur as useful semantics.
- The recurring shape is not enough to justify a generic workflow or `Process` abstraction. Status transitions, operation meaning, recovery, correction, and idempotency ownership remain domain-specific.
- A future reusable abstraction, if ever justified, would need to preserve domain-owned semantics rather than flatten them into generic steps, retries, timers, or compensation.

### Open Questions

- Whether publication targets need durable idempotency contracts beyond PIM process event ledgers remains unresolved.
- The production boundary between PIM correction, target retry, and manual intervention is not defined.
- It remains unknown whether a minimal application utility for snapshot/replay would reduce duplication without becoming a generic process runtime.

### What 2P Disproved

2P disproved that the Order process shape can simply be renamed and reused as a universal business-process model. It also disproved that recurring fields prove generic workflow semantics. The commonality is semantic and architectural, not yet a safe code-reuse boundary.

### Core Impact

`agent-core` was unchanged. Existing `Event`, `Intent`, `Capability`, `Decision`, `Action`, and application-level test records were sufficient. No generic process primitive was proven necessary.

### Architectural Conclusion

The evidence supports **partial generality**. Order and PIM independently need process identity, operation identity, observed outcomes, progress, duplicate-event handling, restart reconstruction, and recovery-required meaning. Their business transitions and recovery actions differ enough that process semantics should remain domain/application-owned for now. This is evidence for architectural knowledge, not permission to add `Process`, `ProcessStore`, or workflow infrastructure to the core.

## Milestone 2Q - Operation Execution Contract & Durability Boundary

### Scenario

The 2Q experiment models an Order process requesting an Inventory reservation. The path is:

```text
Intent -> Capability -> Invocation Event -> Domain Decision
-> Action -> Inventory side effect -> Result Event -> Process state
```

The adversarial case is a successful reservation whose result event is lost, followed by process or domain restart. The process must represent uncertainty instead of inventing success or failure.

### Execution Model

`CapabilityInvoker` resolves a capability and publishes one invocation event. It does not execute an Action, wait for a result, retry, deduplicate, or infer a side effect. Inventory decides whether a reservation is allowed, executes the Inventory-owned effect, and owns evidence that the reservation exists. The Order process records requests and observations and makes explicit recovery decisions.

### Identity Model

```text
event_id       one event envelope/delivery identity
correlation_id groups events belonging to one business interaction
process_id     one business process instance
operation_id   one intended business operation
attempt        one execution attempt for that operation
action_id      one generated execution artifact
causation_id   immediate predecessor identity
```

These identities are not interchangeable. In particular, a correlation ID is not an idempotency key, and an event or action ID does not prove that the intended business operation occurred.

### Operation Lifecycle

The experiment distinguishes `REQUESTED`, `UNKNOWN`, `SUCCEEDED`, `FAILED`, `CONFLICT`, and `STALE` observations. Request acceptance, execution, completion, result publication, and result observation are separate moments:

```text
request published
    != request accepted
    != side effect executed
    != result published
    != result observed by Order
```

When a result is lost after a successful side effect, the process is `UNKNOWN`. A durable process snapshot can reconstruct what Order knew, but it cannot prove Inventory's side effect. A retry is safe only when the Inventory business invariant recognizes the same `operation_id`; a different operation ID represents a different intended reservation.

### Failure Matrix

| Failure point | Process knows | Domain knows | Correct interpretation |
| --- | --- | --- | --- |
| Before invocation | No operation observation | Nothing requested | No execution claim |
| After request event | Request was published | May not have accepted it | Requested, not completed |
| After domain acceptance | Request accepted if acknowledged | Domain has accepted work | Not proof of side effect |
| After side effect, before result | No result observed | Side effect may be completed | `UNKNOWN`; reconcile explicitly |
| After result publication | Result may exist in transport | Domain has completion evidence | Process still needs observation |
| Process crash before result record | Snapshot lacks completion | Domain may have completed | Reconstruct as unknown |
| Domain crash before result publication | Domain may or may not retain effect evidence | Depends on domain durability | Execution state unresolved without domain evidence |
| Duplicate request, same operation ID | Same logical operation | Inventory invariant rejects/reuses effect | Domain-specific duplicate safety |
| Duplicate result, same event ID | Same observation | No new effect | Process event ledger ignores it |
| Stale result from prior attempt | Older attempt identified | May be valid for old attempt | Do not complete current attempt |
| Conflicting results | Success and failure evidence conflict | Authority is unclear | `CONFLICT`; reconciliation required |

### Operation Execution Contract

- **Identity:** `operation_id` uniquely identifies the intended business operation within its process. Attempts and event IDs remain separate.
- **Acceptance:** the target domain may acknowledge receipt or acceptance, but acceptance is not execution.
- **Execution:** only the owning domain can determine whether its side effect was applied.
- **Completion:** completion is authoritative only when the domain produces a result tied to the operation and attempt, backed by domain effect evidence.
- **Failure:** explicit domain rejection or failure is authoritative for that attempt; absence of a result is not failure.
- **Unknown:** missing result after a request, process crash, or domain crash leaves execution `UNKNOWN` until domain evidence or reconciliation resolves it.
- **Duplicate:** the domain owner enforces duplicate business safety for the same operation identity. The process separately deduplicates repeated result event identities.
- **Result:** a result must identify the operation, attempt, event identity, and causation, while retaining the interaction correlation.
- **Recovery:** resolving `UNKNOWN`, retrying, or requesting a release is an explicit Order decision expressed through ordinary capability/event interaction. No generic recovery engine is implied.

### Reconciliation authority

The reconciliation query shape is demonstrated: Order asks an
Inventory-owned capability about a stable operation ID, and Inventory answers
from its effect evidence with the closed vocabulary `EXISTS`, `ABSENT`,
`CONFLICT`, or `STILL_UNKNOWN`. The release-side reference applies the same
shape to a lost release result. `EXISTS` alone confirms that a release occurred;
`ABSENT` means only that no release evidence exists, not that the reservation is
safe to ignore. The exact conformance tests are
[`test_pharmacy_reconciliation.py`](tests/test_pharmacy_reconciliation.py) and
[`test_pharmacy_release_reconciliation.py`](tests/test_pharmacy_release_reconciliation.py).

### Business Semantics

Inventory is authoritative for reservation existence, reserved quantity, and reservation status. Order is authoritative for purchase progression and whether a reservation result has been observed. Order remembering `SUCCEEDED` is not proof that Inventory still has or ever had the reservation.

### Process Semantics

The process records requested operations, attempts, result observations, stale/conflicting evidence, duplicate event IDs, and recovery status. Process durability means reconstructing what the process knew. It does not mean reconstructing whether the Inventory side effect occurred.

### Infrastructure Semantics

Event durability means an event envelope was persisted or delivered; it does not prove side-effect durability. Side-effect durability belongs to the domain that owns the effect. Runtime restart, persistence, transport redelivery, and timeout measurement are infrastructure mechanics. None may infer business success or compensation from absence alone.

### Verified

- A lost result after a successful Inventory effect remains `UNKNOWN` to the process.
- Process identity, correlation, operation, event, causation, attempt, and action identities have distinct meanings.
- Duplicate operation identity can be made business-safe by the Inventory-owned invariant without global message deduplication.
- Duplicate result event identity is separately handled by the process ledger.
- A stale prior-attempt result does not complete the current attempt.
- Conflicting results are retained as `CONFLICT`, not silently resolved.
- A durable process snapshot does not establish operation or side-effect durability.
- `CapabilityInvoker` publishes a request but does not execute an operation.
- No core changes or generic execution infrastructure were required.

### Inference

- Process durability, operation durability, event durability, and side-effect durability are independent guarantees.
- Idempotency is split: Inventory owns the reservation business invariant, while the process owns observation deduplication and infrastructure may control redelivery.
- Reconciliation is required to turn `UNKNOWN` into a business conclusion; the current architecture intentionally does not provide a generic reconciliation engine.
- The execution contract is domain-sensitive. A read operation, reservation, and publication may define acceptance and completion differently.

### Open Questions

- The domain-owned reconciliation query shape is demonstrated; the production
  durability and cross-process serialization contract of the evidence beneath
  that query remains infrastructure-specific.
- The current generic `Action` contract does not require operation ID or attempt fields, so domain adapters must carry them in their own capability payloads.
- `PostgresAtomicOperationStore` now demonstrates a co-located database
  mutation/operation/outbox transaction. Broker acceptance and result
  observation remain operational concerns; an external effect still cannot join
  that transaction.
- Conflict resolution authority and manual recovery procedures remain domain/application decisions.

### What 2Q Disproved

2Q disproved that a durable process snapshot proves a durable operation, that a published request proves execution, that a missing result proves failure, or that event deduplication proves side-effect idempotency. It also disproved that the existing architecture can honestly claim exactly-once execution.

### Core Impact

`agent-core` was unchanged. Existing `Event`, `Intent`, `Capability`,
`Decision`, `Action`, and application-level ledgers express the observed
semantics. The optional `agent-postgres` atomic operation/outbox adapter adds a
transactional infrastructure path without choosing process, idempotency, or
reconciliation semantics for a domain. A generic core execution store,
idempotency manager, or reconciliation primitive would still prematurely choose
that ownership.

### Architectural Conclusion

The architecture can define an honest operation execution contract, but it
cannot guarantee side-effect completion from process and event state alone. The
safe boundary is: the process records what it requested and observed; the
owning domain proves what it executed; infrastructure transports and persists
evidence without manufacturing business certainty. An optional adapter-level
atomic operation/outbox transaction is justified for co-located database work;
no reusable core execution abstraction is.

## Technology independence

This framework is intentionally technology-independent. It uses only Python
standard-library types and explicit interfaces in `agent-core`. Application
composition, conformance checks, and infrastructure adapters live in separate
packages: `agent-app`, `agent-conformance`, `agent-redpanda`, `agent-delta`,
`agent-postgres`, `agent-lancedb`, `agent-ollama`, `agent-openai`,
`agent-scheduler`, and `agent-enterprise`.

Future adapters may integrate:

- LangChain
- Ollama
- OpenAI
- Delta Lake
- LanceDB
- Waterstream
- Kafka

Those integrations belong in separate adapter layers, not in the core runtime.
