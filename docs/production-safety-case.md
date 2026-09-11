# Production safety case

This document answers a narrow question:

> Under what conditions may a team rely on `domain-agent-framework` for a
> side-effecting production system?

It is not a certification, a substitute for a threat model, or a promise that
installing the packages makes a domain safe. It distinguishes repository-tested
properties from deployment assumptions and domain responsibilities.

## Claim taxonomy

| Label | Meaning |
| --- | --- |
| **Verified** | A repository test exercises the named code path and assertion. |
| **Conditional** | The pattern is implemented, but safety depends on an external/domain control. |
| **Not claimed** | The framework intentionally does not provide or prove this property. |

## Safety claims and boundaries

| Claim | Status | Evidence | Required condition / owner | Failure response |
| --- | --- | --- | --- | --- |
| AI advice does not directly execute an action in the normal runtime path. | **Verified** | [2V](milestone-2V.md), `test_ai_boundary_adversarial.py` | Deterministic policy remains the only decision authority. | Reject unknown/ambiguous capability matches and policy-denied recommendations. |
| An Action requires a Decision through the public factory. | **Verified** | [2V](milestone-2V.md) and `decision_to_action` tests | Applies to public construction paths only. | Treat direct private-module access as a code-integrity incident. |
| Every Decision came from real Policy evaluation. | **Not claimed** | [2V limits](milestone-2V.md#limits) | Local Python code can fabricate a Decision. Use separately deployed Enterprise Policy/Executor services where code is not equally trusted. | Reject untrusted local execution architecture; do not describe it as a security boundary. |
| A signed enterprise command has not been altered, expired, misdirected, replayed, or executed after visible revocation. | **Conditional** | [`agent-enterprise`](../agent-enterprise/README.md) tests | Private key custody, trusted issuer deployment, clock discipline, audience/ownership configuration, shared atomic replay claim, and a tested revocation-propagation SLO. | Fail closed; alert on verifier failures, revocation failures, and replay attempts. |
| A business effect still satisfies the state Policy evaluated. | **Conditional** | [2V TOCTOU boundary](milestone-2V.md#business-state-toctou-boundary) and Inventory references | The target domain performs its own native atomic conditional write using signed preconditions. | Return a domain `CONFLICT`; do not retry or infer success generically. |
| One stable operation ID creates one business effect. | **Conditional** | `agent-conformance` and Delta evidence reference | Effect owner stores an operation ID durably and atomically enough for its writer topology. | Deduplicate; escalate any contradictory evidence as `CONFLICT`. |
| Lost effect results can be resolved honestly. | **Conditional** | [2S](milestone-2S.md), release reconciliation tests | The effect-owning domain retains durable evidence and exposes a read-only reconciliation capability. | Preserve `UNKNOWN`/`STILL_UNKNOWN`; reconcile or escalate, never infer success. |
| Process state survives a restart without silently becoming fresh state. | **Conditional** | [2S durable process snapshot](milestone-2S.md#durable-order-process-snapshot-reference) | Process storage is durable and available. | Block processing on `ProcessStoreUnavailable`; infrastructure retries or operators recover it. |
| Redelivery and ordering do not corrupt a process. | **Conditional** | [2S Redpanda status](milestone-2S.md#redpanda-transport-status) | At-least-once delivery, event/reconciliation identity deduplication, and key-based partitioning where ordering matters. | Leave offsets uncommitted on handler failure; use deduplication and sequence guards after redelivery. |
| A raw `Agent` observation is applied exactly once under broker redelivery. | **Not claimed for `Agent.process()` alone** | `Agent.process()` intentionally remains a small in-process runtime. | Use `PostgresAgentReceiptRunner` only for state-only Agents with `PostgresStateStore`, or use a domain-specific receipt/idempotency contract. | Do not put an Agent with a non-idempotent action executor directly behind a broker consumer. |
| One broker event applies one co-located database effect under redelivery. | **Conditional** | `PostgresEventReceiptStore` unit, PostgreSQL concurrency, and Redpanda crash-before-offset-commit integration tests | The handler performs all effect writes through the supplied PostgreSQL transaction and commits the broker offset only after it returns. | A duplicate receipt skips the mutation; a handler failure rolls back the receipt and leaves the offset for redelivery. |
| One broker event updates state through a state-only raw Agent once under redelivery. | **Conditional** | `PostgresAgentReceiptRunner` unit and Redpanda/PostgreSQL crash-before-offset-commit integration tests | The Agent uses the supplied `PostgresStateStore`, has no action executor, and its receipt store points to the same database. | A duplicate receipt skips Agent processing; a policy/state failure rolls back both the state update and receipt. |
| A co-located domain mutation, operation claim, outcome, and outbox record commit atomically. | **Conditional** | `PostgresAtomicOperationStore` unit and service-backed PostgreSQL atomicity tests | The domain performs all database writes through the supplied PostgreSQL transaction and runs the integration test against its production-like isolation level. Multiple publishers use expiring outbox leases, while a deployment-owned retention job archives confirmed evidence. | Roll back the entire transaction; retry only the domain-owned operation according to its recovery policy. |
| An external provider failure does not create a guessed business result. | **Conditional** | [external effect boundary](external-effect-boundary.md) and `agent-conformance` helpers | The domain persists a stable provider idempotency key and has an authoritative reconciliation route. | Keep `UNKNOWN`; reconcile or escalate. Do not generically retry with a new operation ID. |
| Multiple writers cannot race evidence, replay, or process state. | **Not claimed by Delta references** | Delta adapter and Enterprise limitations | Production store/topology provides cross-process CAS, transaction isolation, or a serialized writer per key. | Refuse unsafe scale-out; route through a serialized partition or use a store with atomic conditional claims. |
| A deployed PostgreSQL adapter schema upgrades safely with new code. | **Conditional** | versioned `agent_postgres.migrate` and `agent_enterprise.migrate` upgrade tests | A deployment-controlled migration job runs and backups/rollback procedures are owned by operations. | Stop rollout on migration failure; do not rely on replica startup to mutate schemas. |
| Recovery actions correctly repair every partial failure. | **Not claimed** | 2S demonstrates patterns, not universal workflow behavior. | Each domain owns compensation/release semantics, retry policy, and escalation authority. | Execute only explicit recovery operations; reconcile afterward. |
| Local application lifecycle telemetry changes no business outcome. | **Verified** | `AgentApp` lifecycle observer tests | Observers are treated as best-effort and receive no event payload by default. | Log observer failure and continue the original processing outcome. |
| Redpanda delivery telemetry changes no broker acknowledgement. | **Verified** | `RedpandaEventDispatcher` lifecycle observer tests | Observers are treated as best-effort and receive no broker record payload. | Log observer failure; retain the original commit/redelivery outcome. |

## Deployment profiles

### Local, trusted application

`AgentApp` is appropriate for a prototype, local tool, or single-process
service whose code is in one trusted deployment boundary. It provides runtime
semantics and the Decision-before-Action discipline; it is **not** a
cryptographic authorization boundary. Use it only when arbitrary code cannot
submit an Action or fabricate a Decision.

### Side-effecting domain service

A domain service that reserves stock, changes entitlements, or calls an
external system must add:

1. stable operation IDs and durable effect evidence;
2. a domain-owned reconciliation capability and an explicit result vocabulary;
3. process/outbox persistence where result delivery can be lost;
4. a native atomic invariant/precondition check at the effect owner; and
5. a co-located atomic operation/outbox transaction when multiple workers
   mutate the same database-backed domain; and
6. the [external effect boundary](external-effect-boundary.md), for every
   third-party side effect; and
7. applicable checks from [`agent-conformance`](../agent-conformance/README.md).

The domain, not `agent-core`, owns the business meaning of `CONFLICT`,
`UNKNOWN`, compensation, and escalation.

### Cross-service, untrusted execution boundary

Use the optional Enterprise Policy/Executor reference only as a starting point.
Production deployment requires separately held signing keys, KMS/HSM-backed
key rotation, service identity (mTLS and/or OIDC), short authorization TTLs,
auditable issuance/execution records, an atomic shared replay store, and a
shared revocation store with a tested propagation SLO. The receiving Executor
must explicitly own the action type, verify authorization before calling the
domain effect handler, and the handler must still enforce signed preconditions
atomically.

The Enterprise Policy reference can validate OIDC JWTs using issuer discovery
and JWKS, including exact issuer, audience, expiry, signature, and configured
scope checks. This is a reference ingress adapter, not a claim that the local
Keycloak Compose profile is production identity infrastructure.

The Enterprise HTTP reference can also require mutual TLS and an exact trusted
client DNS SAN. Production still owns certificate enrollment, renewal,
revocation, CA protection, and the mapping of workload identities to expected
peers.

## Required production gates

Before allowing a side effect to affect customers, money, inventory, access,
or regulated data, record evidence for each applicable gate:

- [ ] Threat model identifies trusted code, services, credentials, and
      attackers.
- [ ] Domain has adopted and extended the
      [`agent-conformance`](../agent-conformance/README.md) checks.
- [ ] Every effect has an operation ID, idempotency rule, and durable evidence.
- [ ] Every lost-result path has a reconciliation owner, vocabulary, and human
      escalation procedure.
- [ ] Every external provider has a stable idempotency identifier and an
      authoritative reconciliation route, or is restricted to human review.
- [ ] Domain conditional writes enforce current business invariants and return
      `CONFLICT` without mutation when stale.
- [ ] Production writer topology has an explicit cross-process serialization or
      CAS/transactional guarantee.
- [ ] Enterprise executors use a shared atomic replay store, have explicit
      action ownership, and meet a tested revocation-propagation SLO.
- [ ] When using `PostgresAtomicOperationStore`, each domain mutation uses the
      supplied transaction connection and the PostgreSQL race test runs against
      a production-like database (`POSTGRES_ATOMIC_DATABASE_URL`).
- [ ] Multiple outbox publishers use `claim_outbox`, have an expiry/lease-loss
      policy, and run a separate monitored retention or archival job for
      confirmed outbox and authorization records.
- [ ] Transport commit, redelivery, ordering, and dead-letter behavior are
      exercised against the production-like broker.
- [ ] Redpanda consumers retain a bounded ingress limit appropriate to the
      topic. The adapter defaults to 1 MiB; an oversized record is represented
      in the terminal DLQ only by its byte count and SHA-256 digest, and the
      source offset advances only after that bounded diagnostic is accepted.
- [ ] Alerts cover stuck `UNKNOWN`/`RECOVERY_REQUIRED` processes, reconciliation
      failures, authorization failures, and repeated replay attempts.
- [ ] Key rotation, audit retention, backup/restore, and incident ownership are
      tested operationally.
- [ ] Staging failure tests cover broker restart, handler crash after an effect,
      unavailable evidence storage, duplicate delivery, stale preconditions,
      and concurrent commands for the same aggregate.

## Operational decision rule

Do not replace an unresolved state with a plausible answer. A missing result,
storage outage, stale authorization, or concurrent write is not permission to
continue. Preserve the explicit state (`UNKNOWN`, `STILL_UNKNOWN`, or
`CONFLICT`), record enough identity and lineage to investigate, and route to
the domain's recovery or escalation policy.

## What this framework provides

The framework provides a small runtime vocabulary, explicit authority
boundaries, adapter ports, conformance helpers, and reference implementations.
It makes unsafe shortcuts visible and testable. It does not remove the need
for domain invariant design, transaction selection, operations, or human
governance.
