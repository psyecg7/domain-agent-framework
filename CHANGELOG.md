# Changelog

## 0.2.0 — 2026-09-11

### Breaking changes

- `CapabilityInvoker`, `Authorizer`, `EventTransport`, and related
  capability-invocation exceptions moved from the removed `agent_application`
  package to `agent_app`. `domain-agent-core` now distributes only
  `agent_core` semantics; application composition belongs to `agent-app`.
- `ConversationalGateway`, response rendering, and their application transport
  protocols moved from `agent_core` to `agent_app`. Core retains the generic
  `Intent`, `Capability`, registry, and interpreter contracts only.

### Added

- `domain-agent-app`: the versioned, local-first public application API.
- `domain-agent-conformance`: test-only callable assertions, an adoption
  template, and a CI-checkable conformance discipline for side-effecting
  domains.
- Delta-backed reference persistence for effect evidence and process state,
  including explicit reconciliation outcomes.
- Optional Enterprise reference components for signed, replay-protected
  cross-service command authorization and domain-owned preconditions, now with
  fail-closed decision revocation, receiving-executor action ownership, and
  PostgreSQL-backed shared replay/revocation stores.
- `PostgresAtomicOperationStore`: an optional adapter-level transaction for a
  domain mutation, operation claim, durable outcome, and outbox record. It is
  accompanied by conformance helpers for duplicate outbox messages and rollback.
- External-effect conformance helpers and a documented provider admission rule:
  stable idempotency identity, `UNKNOWN` on ambiguous transport failure, and
  reconciliation before recovery.
- Release compatibility CI: clean wheel installation and smoke imports on
  Python 3.11–3.13, plus a separate unit/conformance validation job.
- Service-backed CI for the repository Redpanda/PostgreSQL profile, covering
  broker redelivery, transactional receipts, and atomicity on every push and
  pull request.
- Versioned PostgreSQL migration runners for adapter state/operation/outbox/
  receipt tables and Enterprise replay/revocation tables, including a
  legacy-outbox upgrade test.
- Operator migration runbook covering backup, controlled schema upgrade,
  version verification, canary rollout, and additive-only rollback policy.
- `AgentApp` payload-free lifecycle observers and local health counters, with
  observer failure isolated from policy and action processing.
- `RedpandaEventDispatcher` payload-free batch lifecycle observers for polling,
  delivery, acknowledgement, terminal-DLQ routing, duration, and failures.
- Bounded Redpanda ingress diagnostics: consumers default to a 1 MiB record
  limit, and oversized records reach the terminal DLQ as a byte-count and
  SHA-256 diagnostic rather than an unbounded payload copy.
- `scripts/verify_framework.sh` provides one local command for deterministic
  checks plus the optional Redpanda/PostgreSQL integration suite, reporting
  unavailable infrastructure explicitly as skipped.
- A separate development/staging Compose profile for Keycloak, Vault, and
  step-ca integration; it is explicitly not a production security deployment.
- Enterprise Policy OIDC ingress validation using issuer discovery and local
  JWKS signature checks, with fail-closed issuer/audience/expiry/scope rules.
- Optional Enterprise HTTP mTLS contexts requiring trusted client certificates
  and one exact workload DNS-SAN or SPIFFE URI-SAN identity check.
- Vault Transit-backed Enterprise Policy issuer: Ed25519 authorization signing
  can occur without exporting the Policy private key into the process.
- Executor key-ring CLI support and overlap/retirement tests for safe signing
  key rotation.
- HA outbox leases for `PostgresAtomicOperationStore`, plus explicit replay and
  revocation expiry-pruning APIs that keep database garbage collection out of
  the Executor hot path.
- `PostgresEventReceiptStore`: an optional transactional event-receipt claim
  for applying one broker event to one co-located PostgreSQL effect, including
  an opt-in Redpanda crash-before-offset-commit redelivery proof.
- `PostgresAgentReceiptRunner`: a deliberately state-only composition of a raw
  Agent's PostgreSQL observation with an event receipt; Agents with action
  executors are rejected rather than incorrectly treated as transactional.
- Terminal Redpanda failure routing for malformed transport records: a source
  offset may advance only after the configured dead-letter publisher accepts
  the diagnostic copy.
- Enterprise HTTP reference request-body limits: Policy and Executor reject an
  oversized declared body before reading it, with a 1 MiB secure default.

### Changed

- The local Compose stack now starts both Redpanda and PostgreSQL. PostgreSQL
  uses the documented development-only `agent` / `agent` credentials and the
  `agent_atomic` database for the atomicity integration check.
- `local_agent`, `InMemoryStateStore`, `FunctionPolicyEngine`, and
  `FunctionActionExecutor` are no longer exposed from the public
  `agent_core` API. Their private implementation moved to `agent_app._local`.
  `AgentApp` is the supported local-first developer API.
- Dependent packages now constrain their framework dependency to the supported
  `>=0.2.0,<0.3.0` release line. See `docs/release-compatibility.md`.
- Enterprise authorization has a bounded, configurable clock-skew allowance;
  OIDC audit principals now use immutable subject/client identity only.

### Known limits

- The local runtime requires a `Decision` to construct an `Action`, but it
  cannot prove that arbitrary Python code obtained that Decision from a real
  Policy evaluation. Use the Enterprise boundary across deployment trust
  domains.
- Durable evidence, replay claims, and process stores require a production
  backend with cross-process atomicity when executors scale beyond one process.
- Reconciliation, recovery, and atomic business preconditions remain
  domain-owned contracts, validated through conformance tests rather than
  supplied by `agent_core`.
